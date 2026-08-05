"""
移動停利 (Moving Take Profit)

條件 (以掃描日為基準):
1. 今日收盤價格低於昨日收盤價格達 8% (含) 以上。

分類為「賣出/風險提示」訊號 (kind="sell")：用於提醒既有持股該考慮停利/停損出場。
"""
from .base import SignalContext, SignalResult, register_signal

@register_signal(
    key="moving_take_profit",
    label="移動停利",
    description="今日收盤價相較昨日收盤跌幅達 8% 以上",
    kind="sell",
)
def check_moving_take_profit(ctx: SignalContext) -> SignalResult:
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

    # 判定跌幅是否達到 8% 以上 (數值 <= -8.0)
    if drop_pct <= -8.0:
        return SignalResult(
            hit=True,
            detail=f"{ctx.scan_date} 收盤價 {today_close}，相較前日收盤 {prev_close} 跌幅為 {drop_pct:.2f}% => 移動停利訊號觸發！",
            marks=[ctx.scan_date]
        )
    else:
        return SignalResult(
            hit=False,
            detail=f"{ctx.scan_date} 收盤價 {today_close}，相較前日收盤 {prev_close} 跌幅為 {drop_pct:.2f}%，未達 8% 回檔標準",
        )
