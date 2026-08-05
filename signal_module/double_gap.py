"""
雙跳空 (Double Gap)

條件:
- 最近 3 根K線 (含今日/掃描日) 之中，有 2 根出現「向上跳空」
  (該日最低點 Low > 前一交易日最高點 High)
"""
from .base import SignalContext, SignalResult, register_signal

WINDOW = 3
REQUIRED_GAPS = 2


@register_signal(
    key="double_gap",
    label="雙跳空",
    description="最近3根K線中有2根出現向上跳空",
)
def check_double_gap(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()

    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")

    idx = dates.index(ctx.scan_date)
    window_start = max(1, idx - (WINDOW - 1))

    gap_dates = []
    for i in range(window_start, idx + 1):
        cur = df.loc[dates[i]]
        prev = df.loc[dates[i - 1]]
        if cur["Low"] > prev["High"]:
            gap_dates.append(dates[i])

    if len(gap_dates) >= REQUIRED_GAPS:
        return SignalResult(
            hit=True,
            detail=f"近{WINDOW}根K線中共 {len(gap_dates)} 根跳空向上: {', '.join(gap_dates)} => 雙跳空成立",
            marks=gap_dates,
        )

    return SignalResult(
        hit=False,
        detail=f"近{WINDOW}根K線中僅 {len(gap_dates)} 根跳空向上 (需要{REQUIRED_GAPS}根)",
    )
