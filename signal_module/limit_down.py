"""
跌停 (Limit Down)

條件 (以掃描日為基準):
1. 掃描日「當天」的收盤價，相較於前一個交易日的收盤價，跌幅達到或超過 9.5% (作為台股跌停的近似判定)。

分類為「賣出/風險提示」訊號 (kind="sell")：代表當日出現重挫，適合用於停損/風險警示，而非買進依據。
"""
from .base import SignalContext, SignalResult, register_signal

@register_signal(
    key="limit_down",
    label="跌停",
    description="當日收盤價觸及跌停 (相較前日收盤跌幅 >= 9.5%)",
    kind="sell",
)
def check_limit_down(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()

    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")

    idx = dates.index(ctx.scan_date)
    
    # 確保有前一天的資料可以比較
    if idx == 0:
        return SignalResult(hit=False, detail="資料不足，無法取得前一交易日收盤價計算跌幅")

    today_close = df.iloc[idx]["Close"]
    prev_close = df.iloc[idx - 1]["Close"]
    
    # 計算跌幅百分比
    drop_pct = (today_close - prev_close) / prev_close * 100

    # 判定跌幅是否達到 9.5% 以上 (也就是數值 <= -9.5)
    if drop_pct <= -9.5:
        return SignalResult(
            hit=True,
            detail=f"{ctx.scan_date} 收盤價 {today_close}，相較前日收盤 {prev_close} 跌幅為 {drop_pct:.2f}% => 跌停成立",
            marks=[ctx.scan_date]
        )
    else:
        return SignalResult(
            hit=False,
            detail=f"{ctx.scan_date} 收盤價 {today_close}，相較前日收盤 {prev_close} 跌幅為 {drop_pct:.2f}%，未達跌停標準",
        )
