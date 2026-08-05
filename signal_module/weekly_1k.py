"""
周1K 買進訊號

條件：
1. 13 周均線向上：本周 13MA > 上周 13MA。
2. 本周收盤價突破本周之前最近一根黑 K（Close < Open）的最高價。
3. 突破周K必須是有效紅K，排除窄線型：
   紅K實體漲幅至少3%，且實體至少占整根K棒振幅50%。
訊號只在每周最後一個交易日判定，避免同一周重複觸發。
"""

import pandas as pd

from .base import SignalContext, SignalResult, register_signal


# 窄線型過濾門檻，可依策略需要調整。
MIN_RED_BODY_PCT = 3.0
MIN_BODY_TO_RANGE_RATIO = 0.50


@register_signal(
    key="weekly_1k",
    label="周1K",
    description="13周均線向上、有效紅K突破最近黑K高點（排除窄線型）",
)
def check_weekly_1k(ctx: SignalContext) -> SignalResult:
    daily = ctx.df.copy()
    daily.index = pd.to_datetime(daily.index, errors="coerce").normalize()
    daily = daily[~daily.index.isna()].sort_index()
    scan_date = pd.Timestamp(ctx.scan_date).normalize()

    if scan_date not in daily.index:
        return SignalResult(hit=False, detail="掃描日期不在資料範圍內")

    scan_week = scan_date.to_period("W-FRI")
    same_week_dates = daily.index[daily.index.to_period("W-FRI") == scan_week]
    if len(same_week_dates) == 0 or scan_date != same_week_dates.max().normalize():
        return SignalResult(hit=False, detail="尚未到本周最後一個交易日")

    # 僅使用掃描日以前的資料，避免引用未來資料。
    daily = daily.loc[:scan_date]
    weekly = (
        daily.resample("W-FRI")
        .agg({
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        })
        .dropna(subset=["Open", "High", "Low", "Close"])
    )
    weekly["MA13"] = weekly["Close"].rolling(13).mean()

    if len(weekly) < 14 or weekly["MA13"].iloc[-2:].isna().any():
        return SignalResult(hit=False, detail="周線資料不足，需要至少 14 周")

    current = weekly.iloc[-1]
    previous = weekly.iloc[-2]
    prior_weeks = weekly.iloc[:-1]
    bearish_weeks = prior_weeks[prior_weeks["Close"] < prior_weeks["Open"]]
    if bearish_weeks.empty:
        return SignalResult(hit=False, detail="目前找不到本周之前的黑周K")

    latest_bearish_date = bearish_weeks.index[-1]
    latest_bearish = bearish_weeks.iloc[-1]

    cond_ma13_up = current["MA13"] > previous["MA13"]
    cond_break_high = current["Close"] > latest_bearish["High"]
    candle_body = current["Close"] - current["Open"]
    candle_range = current["High"] - current["Low"]
    red_body_pct = candle_body / current["Open"] * 100 if current["Open"] else 0.0
    body_to_range_ratio = candle_body / candle_range if candle_range > 0 else 0.0
    cond_valid_red_k = (
        candle_body > 0
        and red_body_pct >= MIN_RED_BODY_PCT
        and body_to_range_ratio >= MIN_BODY_TO_RANGE_RATIO
    )
    hit = cond_ma13_up and cond_break_high and cond_valid_red_k

    detail = (
        f"13周MA：{current['MA13']:.2f} > {previous['MA13']:.2f} "
        f"({'符合' if cond_ma13_up else '不符合'})；"
        f"周收盤：{current['Close']:.2f} > 最近黑K高點 "
        f"{latest_bearish['High']:.2f}（{latest_bearish_date:%Y-%m-%d}）"
        f" ({'符合' if cond_break_high else '不符合'})；"
        f"有效紅K：實體漲幅 {red_body_pct:.2f}% >= {MIN_RED_BODY_PCT:.1f}%，"
        f"實體占振幅 {body_to_range_ratio * 100:.1f}% >= "
        f"{MIN_BODY_TO_RANGE_RATIO * 100:.0f}% "
        f"({'符合' if cond_valid_red_k else '窄線型，排除'})"
    )

    marks = []
    if hit:
        bearish_daily_dates = daily.index[
            daily.index.to_period("W-FRI") == latest_bearish_date.to_period("W-FRI")
        ]
        if len(bearish_daily_dates):
            marks.append(bearish_daily_dates[-1].strftime("%Y-%m-%d"))
        marks.append(scan_date.strftime("%Y-%m-%d"))

    return SignalResult(hit=hit, detail=detail, marks=marks)
