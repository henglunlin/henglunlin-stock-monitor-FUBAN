"""
漲停 (Limit Up)

條件 (以掃描日為基準):
1. 掃描日「當天」的收盤價，相較於前一個交易日的收盤價，漲幅達到或超過 9.5% (作為台股漲停的近似判定)。
"""
from .base import SignalContext, SignalResult, register_signal

@register_signal(
    key="limit_up",
    label="漲停",
    description="當日收盤價觸及漲停 (相較前日收盤漲幅 >= 9.5%)",
)
def check_limit_up(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()

    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")

    idx = dates.index(ctx.scan_date)
    
    # 確保有前一天的資料可以比較
    if idx == 0:
        return SignalResult(hit=False, detail="資料不足，無法取得前一交易日收盤價計算漲幅")

    today_close = df.iloc[idx]["Close"]
    prev_close = df.iloc[idx - 1]["Close"]
    
    # 計算漲幅百分比
    rise_pct = (today_close - prev_close) / prev_close * 100

    # 判定漲幅是否達到 9.5% 以上
    if rise_pct >= 9.5:
        return SignalResult(
            hit=True,
            detail=f"{ctx.scan_date} 收盤價 {today_close}，相較前日收盤 {prev_close} 漲幅為 {rise_pct:.2f}% => 漲停成立",
            marks=[ctx.scan_date]
        )
    else:
        return SignalResult(
            hit=False,
            detail=f"{ctx.scan_date} 收盤價 {today_close}，相較前日收盤 {prev_close} 漲幅為 {rise_pct:.2f}%，未達漲停標準",
        )
