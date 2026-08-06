"""
下降趨勢線突破 (預先計算版)

跟 trendline_breakout.py 判斷的是同一件事(上緣凸包下降趨勢線、收盤價向上突破)，
但這個版本不在盤中即時運算，而是讀取「昨晚收盤後就先算好」的突破價位——
repo 根目錄的 trendline_levels.json，由 precompute_trendlines.py 搭配
GitHub Actions 排程每天更新。

盤中掃描時只需要拿「目前價格」跟這個預先算好的數字比較即可，
不用每次刷新都重新跑一次上緣凸包演算法，大幅減少盤中運算量。

安全機制:
    - 找不到 trendline_levels.json 時，一律視為不成立 (不會噴錯，也不會亂猜)。
    - 檔案裡的 target_date 對不上「今天」時 (可能排程還沒跑、今天沒開盤、
      或忘了更新)，一律視為不成立，不會誤用到舊資料。
    - 這兩種情況的原因都會寫進 detail，方便排查。

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

    symbol_levels = (levels_data.get("levels") or {}).get(ctx.code)
    if not symbol_levels:
        return SignalResult(hit=False, detail=f"{ctx.code} 沒有預先計算好的下降趨勢線資料")

    today_close = float(df.loc[ctx.scan_date, "Close"])

    hit_tiers = []
    detail_lines = []

    for tier_key, tier_label in TIER_LABELS:
        info = symbol_levels.get(tier_key)
        if not info:
            detail_lines.append(f"【{tier_label}】昨晚找不到合法的下降趨勢線")
            continue
        breakout_price = info.get("breakout_price")
        if breakout_price is None:
            detail_lines.append(f"【{tier_label}】預算資料缺少突破價位")
            continue
        if today_close > breakout_price:
            hit_tiers.append(tier_label)
            a1 = info.get("anchor1_date", "-")
            a2 = info.get("anchor2_date", "-")
            detail_lines.append(
                f"【{tier_label}】✅現價{today_close:.2f} > 昨晚預算突破價{breakout_price:.2f} "
                f"(錨點 {a1}→{a2})"
            )
        else:
            detail_lines.append(
                f"【{tier_label}】現價{today_close:.2f} 尚未突破昨晚預算突破價{breakout_price:.2f}"
            )

    if hit_tiers:
        summary = "、".join(hit_tiers)
        detail = f"{ctx.scan_date} 突破下降趨勢線：{summary}\n" + "\n".join(detail_lines)
        return SignalResult(hit=True, detail=detail, marks=[ctx.scan_date], sub_label=f"({summary})")

    return SignalResult(hit=False, detail="\n".join(detail_lines))
