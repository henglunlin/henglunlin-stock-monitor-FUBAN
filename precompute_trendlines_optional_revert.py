"""
precompute_trendlines.py
==========================
每日排程用: 讀取 stock_groups.json 裡的所有股票代碼，用 twse_ohlcv.db 裡
「今天」為止的歷史資料，算出「下一個交易日」三個等級(短期/中短期/中長期)
下降趨勢線各自的延伸突破價位，存成 trendline_levels.json 並提交回 repo。

盤中掃描時不需要重新跑這個上緣凸包演算法，signal_module/precomputed_trendline_breakout.py
只要讀這個 JSON、拿目前價格比較即可，大幅減少盤中運算量。

跟 signal_module/trendline_breakout.py (即時運算版，用在「台股掃描器」repo) 判斷的是
同一件事、用同一套演算法，這裡為了讓這支腳本可以獨立執行(不依賴 Streamlit / signal_module
套件的匯入路徑)，把核心的上緣凸包演算法重新複製了一份，兩邊邏輯需要修改時記得同步。

用法:
    python precompute_trendlines.py
(由 GitHub Actions 排程或手動觸發執行；本機也可以直接跑來測試)
"""
import json
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests

DB_PATH = "twse_ohlcv.db"
GROUPS_FILE = "stock_groups.json"
OUTPUT_FILE = "trendline_levels.json"

# ===== Telegram 推播設定 =====
# 跟 update.yml / app.py 一樣，走環境變數讀 GitHub Secrets，本機沒設也不會出錯，只是不推播。
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ===== 跟 signal_module/trendline_breakout.py 完全相同的參數/定義，請保持同步 =====
SHORT_MAX_DAYS = 6
MID_MAX_DAYS = 23
LONG_MAX_DAYS = 66  # 一季，最常用的長期掃描區間
MIN_ANCHOR_GAP_DAYS = 2
MIN_BREAKOUT_GAP_DAYS = 2

TIER_DEFS = [
    ("short", 0, SHORT_MAX_DAYS, "短期"),
    ("mid", SHORT_MAX_DAYS, MID_MAX_DAYS, "中短期"),
    ("long", MID_MAX_DAYS, LONG_MAX_DAYS, "中長期"),
]


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _upper_hull(points):
    hull = []
    for p in points:
        while len(hull) >= 2 and _cross(hull[-2], hull[-1], p) >= 0:
            hull.pop()
        hull.append(p)
    return hull


def _hull_edges(highs, end_idx, lookback, min_gap):
    start_pos = max(0, end_idx - lookback)
    positions = list(range(start_pos, end_idx))
    if len(positions) < 2:
        return []
    points = [(pos, float(highs[pos])) for pos in positions]
    hull = _upper_hull(points)
    edges = []
    for i in range(len(hull) - 1):
        (x1, y1), (x2, y2) = hull[i], hull[i + 1]
        if y2 < y1 and (x2 - x1) > min_gap:
            edges.append((x1, x2, y1, y2))
    return edges


def _select_edge(edges, end_idx, min_days, max_days):
    candidates = [
        e for e in edges
        if min_days < (end_idx - e[0]) <= max_days
        and (end_idx - e[1]) >= MIN_BREAKOUT_GAP_DAYS
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e[1])


def send_telegram_message(text: str):
    """跟 app.py 裡的 send_telegram_message 邏輯一致：沒設定 token/chat_id 就靜靜跳過，不影響主流程。"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，略過 Telegram 推播")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            print(f"Telegram 推播失敗: HTTP {res.status_code} {res.text[:200]}")
    except Exception as e:
        print(f"Telegram 推播失敗: {e}")


def next_trading_day_guess(last_date: pd.Timestamp) -> str:
    """
    簡單用「跳過六日」猜下一個交易日，不含台股國定假日判斷。
    如果猜錯(例如遇到連假)，最多就是那天的 target_date 對不上，
    signal_module 那邊會直接判定「資料對不上今天」而不觸發，不會誤用到舊資料，是安全的。
    """
    d = last_date + pd.Timedelta(days=1)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d = d + pd.Timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def load_symbols_from_groups(groups_file: str) -> list:
    if not os.path.exists(groups_file):
        raise FileNotFoundError(f"找不到 {groups_file}")
    with open(groups_file, "r", encoding="utf-8") as f:
        groups = json.load(f)
    symbols = set()
    for stocks in groups.values():
        for s in stocks:
            symbols.add(s)
    return sorted(symbols)


def load_history(conn, symbol: str) -> pd.DataFrame:
    code = symbol.split(".")[0]
    query = "SELECT Date, Open, High, Low, Close, Volume FROM ohlcv_data WHERE SecurityCode = ? ORDER BY Date"
    df = pd.read_sql(query, conn, params=[code])
    if df.empty:
        return df
    df["Date"] = pd.to_datetime(df["Date"])
    return df.dropna(subset=["Date", "High", "Close"]).sort_values("Date").reset_index(drop=True)


def compute_symbol_levels(df: pd.DataFrame) -> dict:
    """
    df: 該股票的歷史資料，最後一筆視為「今天」(twse_ohlcv.db 已同步到今天收盤)。
    回傳: {"short": {...}或None, "mid": {...}或None, "long": {...}或None}
    """
    n = len(df)
    result = {"short": None, "mid": None, "long": None}
    if n < 10:
        return result

    today_idx = n - 1  # 今天 = 資料最後一筆 (跟 trendline_breakout.py 的 end_idx 定義一致)
    highs = df["High"].values

    for tier_key, min_days, max_days, tier_label in TIER_DEFS:
        edges = _hull_edges(highs, today_idx, LONG_MAX_DAYS, MIN_ANCHOR_GAP_DAYS)
        edge = _select_edge(edges, today_idx, min_days, max_days)
        if edge is None:
            continue

        x1, x2, y1, y2 = edge
        slope = (y2 - y1) / (x2 - x1)
        intercept = y1 - slope * x1

        # 把同一條線再往後延伸一天 (明天 = today_idx + 1)，算出明天的突破價位
        tomorrow_idx = today_idx + 1
        breakout_price = slope * tomorrow_idx + intercept

        result[tier_key] = {
            "breakout_price": round(float(breakout_price), 2),
            "anchor1_date": df["Date"].iloc[x1].strftime("%Y-%m-%d"),
            "anchor2_date": df["Date"].iloc[x2].strftime("%Y-%m-%d"),
            "anchor1_high": round(float(y1), 2),
            "anchor2_high": round(float(y2), 2),
            "tier_label": tier_label,
        }

    return result


def main():
    tw_now = datetime.now(ZoneInfo("Asia/Taipei"))
    computed_date = tw_now.strftime("%Y-%m-%d")

    if not os.path.exists(DB_PATH):
        raise FileNotFoundError(f"找不到 {DB_PATH}，請確認 twse_ohlcv.db 已經同步到 repo 根目錄")

    symbols = load_symbols_from_groups(GROUPS_FILE)
    print(f"共 {len(symbols)} 檔股票需要計算 (來源: {GROUPS_FILE})")

    levels = {}
    errors = []
    target_date = None

    with sqlite3.connect(DB_PATH) as conn:
        for symbol in symbols:
            try:
                df = load_history(conn, symbol)
                if df.empty:
                    errors.append(f"{symbol}: 資料庫查無資料")
                    continue

                # target_date 用「資料庫裡實際最後一天」往後推算下一個交易日，
                # 不用電腦的系統時間去猜，這樣就算排程晚一點跑、或資料庫還沒同步到最新，
                # 算出來的 target_date 也一定跟資料本身一致，不會誤標成錯的日期。
                last_date = df["Date"].iloc[-1]
                symbol_target_date = next_trading_day_guess(last_date)
                if target_date is None:
                    target_date = symbol_target_date

                levels[symbol] = compute_symbol_levels(df)
            except Exception as e:
                errors.append(f"{symbol}: {type(e).__name__}: {e}")

    output = {
        "computed_date": computed_date,
        "target_date": target_date,
        "generated_at": tw_now.strftime("%Y-%m-%d %H:%M:%S"),
        "symbol_count": len(levels),
        "levels": levels,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n已寫入 {OUTPUT_FILE}")
    print(f"computed_date(算的當下)={computed_date}  target_date(給哪天用)={target_date}")
    print(f"成功計算 {len(levels)} 檔股票")

    hit_any = sum(1 for v in levels.values() if any(v.get(t) for t in ("short", "mid", "long")))
    print(f"其中有找到至少一條合法趨勢線的股票數: {hit_any}")

    if errors:
        print(f"\n{len(errors)} 檔股票計算失敗:")
        for e in errors[:30]:
            print(f"  - {e}")
        if len(errors) > 30:
            print(f"  ...(還有 {len(errors) - 30} 筆，省略)")

    # ===== Telegram 完成通知 =====
    msg_lines = [
        "📐 <b>下降趨勢線每日預算完成</b>",
        "",
        f"📅 算的當下：{computed_date}",
        f"🎯 給哪天用：{target_date}",
        f"📊 共處理 {len(symbols)} 檔股票，成功 {len(levels)} 檔",
        f"📈 找到至少一條合法趨勢線：{hit_any} 檔",
    ]
    if errors:
        msg_lines.append(f"⚠️ 計算失敗：{len(errors)} 檔")
        for e in errors[:5]:
            msg_lines.append(f"　- {e}")
        if len(errors) > 5:
            msg_lines.append(f"　...(還有 {len(errors) - 5} 筆，詳見 Action log)")
    send_telegram_message("\n".join(msg_lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # 就算整支腳本掛掉(例如找不到 twse_ohlcv.db)，也要推播讓你知道排程失敗了，
        # 而不是默默地讓 trendline_levels.json 停在舊資料、卻沒有人發現。
        send_telegram_message(f"❌ <b>下降趨勢線每日預算失敗</b>\n\n{type(e).__name__}: {e}")
        raise
