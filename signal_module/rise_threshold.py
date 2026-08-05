"""
漲幅達標

條件:
- 掃描日(今日)收盤價相較「前一交易日」收盤價之漲幅 >= 門檻(%)
- 門檻預設 5%，可由呼叫端透過 ctx.params["rise_threshold"] 動態帶入
  (對應主程式側邊「儀表板漲幅達標門檻」設定)
"""
from .base import SignalContext, SignalResult, register_signal

DEFAULT_THRESHOLD = 5.0


@register_signal(
    key="rise_threshold",
    label="漲幅達標",
    description="當日收盤漲幅達到或超過門檻(預設5%，可於掃描設定調整)",
    kind="buy",
)
def check_rise_threshold(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()

    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")

    idx = dates.index(ctx.scan_date)
    if idx == 0:
        return SignalResult(hit=False, detail="資料不足，無法計算漲幅")

    today_close = df.iloc[idx]["Close"]
    prev_close = df.iloc[idx - 1]["Close"]
    if prev_close == 0:
        return SignalResult(hit=False, detail="前一日收盤價為0，無法計算漲幅")

    pct = (today_close - prev_close) / prev_close * 100
    threshold = float(ctx.params.get("rise_threshold", DEFAULT_THRESHOLD)) if ctx.params else DEFAULT_THRESHOLD

    if pct >= threshold:
        return SignalResult(
            hit=True,
            detail=(
                f"{ctx.scan_date} 收盤 {today_close:.2f}，較前日({prev_close:.2f})上漲 "
                f"{pct:.2f}%，達門檻 {threshold:.1f}% => 漲幅達標"
            ),
            marks=[ctx.scan_date],
        )
    return SignalResult(
        hit=False,
        detail=f"{ctx.scan_date} 漲幅 {pct:.2f}%，未達門檻 {threshold:.1f}%",
    )
