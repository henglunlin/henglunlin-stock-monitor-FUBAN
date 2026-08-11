"""
下降趨勢線突破 (預先計算版)

跟 trendline_breakout.py 判斷的是同一件事(上緣凸包下降趨勢線、收盤價向上突破)，
但這個版本不在盤中即時運算，而是讀取「昨晚收盤後就先算好」的突破價位——
repo 根目錄的 trendline_levels.json，由 precompute_trendlines.py 搭配
GitHub Actions 排程每天更新。

盤中掃描時只需要拿「目前價格」跟這個預先算好的數字比較即可，
不用每次刷新都重新跑一次上緣凸包演算法，大幅減少盤中運算量。

額外條件 (2026-08-10 新增):
    突破當日還必須「收盤漲幅 >= MIN_PCT_GAIN(預設2.5%)」才算數，只是突破預算價位
    但漲幅不夠的情況不會觸發訊號(detail 裡仍會記錄突破了、只是漲幅不足)。
    這個門檻是手動指定的經驗值，不是本次進場指標濾網優化分析回測驗證過的結果——
    如果之後想確認2.5%是不是最佳切點，可以比照該次分析的 walk-forward 流程，
    對這個訊號額外做一次數值化門檻掃描。

安全機制:
    - 找不到 trendline_levels.json 時，一律視為不成立 (不會噴錯，也不會亂猜)。
    - 檔案裡的 target_date 對不上「今天」時 (可能排程還沒跑、今天沒開盤、
      或忘了更新)，一律視為不成立，不會誤用到舊資料。
    - 沒有前一交易日資料、無法計算漲幅時，一律視為不成立(不會誤判漲幅達標)。
    - 這些情況的原因都會寫進 detail，方便排查。

依存檔案:
    - repo 根目錄 trendline_levels.json (由 precompute_trendlines.py 產生並提交)
"""
import json
import os

from .base import SignalContext, SignalResult, register_signal

LEVELS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trendline_levels.json"
)

TIER_LABELS = [("short", "短期"), ("mid", "中短期"), ("long", "中長期")]

# 額外條件：當日漲幅至少要達到這個百分比，才算數(用今日收盤 vs 前一交易日收盤計算)。
# 這個門檻是使用者手動指定，不是本次回測濾網分析驗證過的結果，之後若要調整可回頭
# 用同一套 walk-forward 流程驗證看看不同門檻值的效果。
MIN_PCT_GAIN = 2.5

# 簡單的檔案內容快取: 只有在檔案的修改時間變了才重新讀取，
# 避免同一次掃描(可能上百檔股票)每一檔都重新開一次檔案、重新解析一次 JSON。
_cache = {"mtime": None, "data": None}


def _load_levels():
    if not os.path.exists(LEVELS_FILE):
        return None
    try:
        mtime = os.path.getmtime(LEVELS_FILE)
    except OSError:
        return None
    if _cache["mtime"] == mtime and _cache["data"] is not None:
        return _cache["data"]
    try:
        with open(LEVELS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    _cache["mtime"] = mtime
    _cache["data"] = data
    return data


def _lookup_symbol_levels(levels: dict, code: str):
    """
    用股票代碼查 levels 字典，容忍「有沒有帶 .TW/.TWO 後綴」的格式差異。
    正常情況下 ctx.code 應該跟 JSON 裡的 key 格式完全一致(例如都是 "3711.TW")，
    但為了避免任何一邊的代碼格式稍有出入(例如少打了後綴、大小寫不同)就整個查不到，
    這裡多做幾層寬鬆比對:
      1. 完全相同的 key，直接命中(最常見、最快)。
      2. 忽略大小寫比對一次。
      3. 如果還是找不到，改用「去掉 .TW/.TWO 後綴的純代碼」去比對 levels 裡每一個 key，
         只要純代碼相同就視為同一檔股票。
    """
    if not levels or not code:
        return None

    if code in levels:
        return levels[code]

    code_upper = str(code).strip().upper()
    for key, value in levels.items():
        if str(key).strip().upper() == code_upper:
            return value

    bare_code = code_upper.split(".")[0]
    for key, value in levels.items():
        if str(key).strip().upper().split(".")[0] == bare_code:
            return value

    return None


@register_signal(
    key="precomputed_trendline_breakout",
    label="下降趨勢線突破",
    description="讀取每日排程預先算好的下降趨勢線突破價位(短期/中短期/中長期)，比對目前價格是否已突破",
)
def check_precomputed_trendline_breakout(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()
    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")

    levels_data = _load_levels()
    if levels_data is None:
        return SignalResult(
            hit=False,
            detail=f"找不到預先計算好的 {os.path.basename(LEVELS_FILE)}，尚無法判定(請確認每日排程有正常執行)",
        )

    target_date = levels_data.get("target_date")
    if target_date != ctx.scan_date:
        return SignalResult(
            hit=False,
            detail=(
                f"預先計算的資料是給 {target_date} 用的，跟今天掃描日({ctx.scan_date})對不上"
                f"(可能是排程還沒跑、今天沒開盤、或忘了更新)，暫不判定"
            ),
        )

    symbol_levels = _lookup_symbol_levels(levels_data.get("levels") or {}, ctx.code)
    if not symbol_levels:
        return SignalResult(hit=False, detail=f"{ctx.code} 沒有預先計算好的下降趨勢線資料")

    today_close = float(df.loc[ctx.scan_date, "Close"])

    # 計算當日漲幅 (今日收盤 vs 前一交易日收盤)，用來套用「漲幅至少2.5%」的門檻
    scan_idx = dates.index(ctx.scan_date)
    if scan_idx == 0:
        pct_gain = None  # 沒有前一交易日資料，無法計算漲幅
    else:
        prev_close = float(df.loc[dates[scan_idx - 1], "Close"])
        pct_gain = (today_close - prev_close) / prev_close * 100 if prev_close else None

    if pct_gain is None:
        gain_ok = False
        gain_note = "漲幅無法計算(無前一交易日收盤資料)"
    else:
        gain_ok = pct_gain >= MIN_PCT_GAIN
        gain_note = f"今日漲幅{pct_gain:+.2f}% {'✅達標' if gain_ok else f'未達{MIN_PCT_GAIN}%門檻'}"

    hit_tiers = []
    detail_lines = [gain_note]

    for tier_key, tier_label in TIER_LABELS:
        info = symbol_levels.get(tier_key)
        if not info:
            detail_lines.append(f"【{tier_label}】昨晚找不到合法的下降趨勢線")
            continue
        breakout_price = info.get("breakout_price")
        if breakout_price is None:
            detail_lines.append(f"【{tier_label}】預算資料缺少突破價位")
            continue
        if today_close > breakout_price and gain_ok:
            hit_tiers.append(tier_label)
            a1 = info.get("anchor1_date", "-")
            a2 = info.get("anchor2_date", "-")
            detail_lines.append(
                f"【{tier_label}】✅現價{today_close:.2f} > 昨晚預算突破價{breakout_price:.2f}，"
                f"且漲幅{pct_gain:+.2f}%達標 (錨點 {a1}→{a2})"
            )
        elif today_close > breakout_price and not gain_ok:
            detail_lines.append(
                f"【{tier_label}】現價{today_close:.2f} 已突破預算突破價{breakout_price:.2f}，"
                f"但{gain_note}，不算數"
            )
        else:
            detail_lines.append(
                f"【{tier_label}】現價{today_close:.2f} 尚未突破昨晚預算突破價{breakout_price:.2f}"
            )

    if hit_tiers:
        summary = "、".join(hit_tiers)
        detail = f"{ctx.scan_date} 突破下降趨勢線：{summary}\n" + "\n".join(detail_lines)
        try:
            return SignalResult(hit=True, detail=detail, marks=[ctx.scan_date], sub_label=f"({summary})")
        except TypeError:
            # signal_module/base.py 裡的 SignalResult 還沒有 sub_label 這個欄位
            # (需要更新成有支援 sub_label 的版本，才能在「訊號類型」欄位自動標示
            # 是短期/中短期/中長期)。這裡優雅退回不帶 sub_label 的版本，
            # 至少訊號還是會正確觸發、正確顯示成立，只是暫時不會自動標示等級——
            # 等級資訊仍然完整寫在上面的 detail 文字裡，不會遺失。
            return SignalResult(hit=True, detail=detail, marks=[ctx.scan_date])

    return SignalResult(hit=False, detail="\n".join(detail_lines))
