"""
單跳空 (Single Gap)

條件:
- 掃描日(今日)當天出現「向上跳空」：
  今日最低點 (Low) > 昨日最高點 (High)

(改版: 原本會往前看最近3個交易日、只要其中任一天曾經跳空就算成立，
 現在改成只認定「今日」是否真的跳空，避免今天股價已經回落、
 卻因為1~2天前的舊跳空事件還在觀察窗內而誤判成立。)
"""
from .base import SignalContext, SignalResult, register_signal


@register_signal(
    key="single_gap",
    label="單跳空",
    description="今日最低點高於昨日最高點，向上跳空成立",
)
def check_single_gap(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()

    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")

    idx = dates.index(ctx.scan_date)
    if idx == 0:
        return SignalResult(hit=False, detail="資料不足，無法取得前一交易日資料")

    today_low = df.loc[dates[idx], "Low"]
    prev_high = df.loc[dates[idx - 1], "High"]

    if today_low > prev_high:
        return SignalResult(
            hit=True,
            detail=(
                f"{ctx.scan_date} 今日最低點 {today_low:.2f} > "
                f"昨日({dates[idx-1]})最高點 {prev_high:.2f} => 單跳空成立"
            ),
            marks=[ctx.scan_date],
        )

    return SignalResult(
        hit=False,
        detail=f"{ctx.scan_date} 今日最低點 {today_low:.2f} 未高於昨日最高點 {prev_high:.2f}，不成立",
    )
