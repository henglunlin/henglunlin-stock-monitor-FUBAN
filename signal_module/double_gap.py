"""
雙跳空 (Double Gap)

條件:
- 掃描日(今日)當天出現「向上跳空」：今日最低點 (Low) > 昨日最高點 (High)
- 且昨日相對前日「也」出現向上跳空：昨日最低點 (Low) > 前日最高點 (High)
  (代表連續兩個交易日都跳空向上，動能比單跳空更強)

(改版: 原本是「最近3個交易日內有2天跳空即可、不要求連續、也不要求含今日」，
 現在改成必須是「今日」跳空、且緊接著昨日也跳空的「連續兩日」型態，
 避免今天股價已經回落、卻因為抓到過去不連續的兩次舊跳空而誤判成立。)
"""
from .base import SignalContext, SignalResult, register_signal


@register_signal(
    key="double_gap",
    label="雙跳空",
    description="今日與昨日連續兩個交易日皆向上跳空(今日最低>昨日最高，且昨日最低>前日最高)",
)
def check_double_gap(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()

    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")

    idx = dates.index(ctx.scan_date)
    if idx < 2:
        return SignalResult(hit=False, detail="資料不足，無法取得前兩個交易日資料")

    today_low = df.loc[dates[idx], "Low"]
    yest_high = df.loc[dates[idx - 1], "High"]
    yest_low = df.loc[dates[idx - 1], "Low"]
    prev2_high = df.loc[dates[idx - 2], "High"]

    gap_today = today_low > yest_high
    gap_yesterday = yest_low > prev2_high

    if gap_today and gap_yesterday:
        return SignalResult(
            hit=True,
            detail=(
                f"{ctx.scan_date} 今日最低 {today_low:.2f} > 昨日最高 {yest_high:.2f}，"
                f"且昨日({dates[idx-1]})最低 {yest_low:.2f} > 前日最高 {prev2_high:.2f} "
                f"=> 雙跳空成立(連續兩日跳空)"
            ),
            marks=[dates[idx - 1], ctx.scan_date],
        )

    if not gap_today:
        reason = f"{ctx.scan_date} 今日最低 {today_low:.2f} 未高於昨日最高 {yest_high:.2f}"
    else:
        reason = (
            f"今日雖跳空，但昨日({dates[idx-1]})最低 {yest_low:.2f} "
            f"未高於前日最高 {prev2_high:.2f}，非連續兩日跳空"
        )

    return SignalResult(hit=False, detail=f"{reason}，雙跳空不成立")
