"""
KD高腳

條件:
- 掃描日(今日)出現 KD 黃金交叉 (今日 K > D，且前一日 K <= D)
- 往前 30 個交易日內，曾出現另一次黃金交叉，且該次交叉的 K 值 < 50 (低檔黃金交叉)
- 若今日交叉的 K 值 高於 該次低檔交叉的 K 值 => KD高腳成立
  (代表低檔越墊越高，動能增強)

需要 SignalContext.df 已包含 K / D 欄位 (見 indicators.add_indicators)
"""
from .base import SignalContext, SignalResult, register_signal

LOOKBACK_DAYS = 30
LOW_K_THRESHOLD = 50.0


def _is_golden_cross(df, i) -> bool:
    if i == 0:
        return False
    k_prev, d_prev = df["K"].iloc[i - 1], df["D"].iloc[i - 1]
    k_cur, d_cur = df["K"].iloc[i], df["D"].iloc[i]
    return k_prev <= d_prev and k_cur > d_cur


@register_signal(
    key="kd_gao_jiao",
    label="KD高腳",
    description="今日KD黃金交叉，且K值高於前30個交易日內K<40的黃金交叉",
)
def check_kd_gao_jiao(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()

    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")
    if "K" not in df.columns or "D" not in df.columns:
        return SignalResult(hit=False, detail="資料缺少 K/D 指標欄位")

    idx = dates.index(ctx.scan_date)
    if not _is_golden_cross(df, idx):
        return SignalResult(hit=False, detail="掃描日未出現KD黃金交叉，不成立")

    today_k = df["K"].iloc[idx]
    lookback_start = max(1, idx - LOOKBACK_DAYS)

    prev_cross_date, prev_cross_k = None, None
    for i in range(idx - 1, lookback_start - 1, -1):
        if _is_golden_cross(df, i):
            k_val = df["K"].iloc[i]
            if k_val < LOW_K_THRESHOLD:
                prev_cross_date, prev_cross_k = dates[i], k_val
                break

    if prev_cross_date is None:
        return SignalResult(
            hit=False,
            detail=f"掃描日雖出現黃金交叉，但前{LOOKBACK_DAYS}個交易日內未找到K<{LOW_K_THRESHOLD:.0f}的黃金交叉可比較",
        )

    if today_k > prev_cross_k:
        return SignalResult(
            hit=True,
            detail=(
                f"{ctx.scan_date} 黃金交叉 K={today_k:.1f}，"
                f"高於 {prev_cross_date} 低檔黃金交叉 K={prev_cross_k:.1f} => KD高腳成立"
            ),
            marks=[prev_cross_date, ctx.scan_date],
        )

    return SignalResult(
        hit=False,
        detail=(
            f"{ctx.scan_date} 黃金交叉 K={today_k:.1f}，"
            f"未高於 {prev_cross_date} 黃金交叉 K={prev_cross_k:.1f}，不成立"
        ),
    )
