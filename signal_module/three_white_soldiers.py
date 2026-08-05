"""
三白兵 (Three White Soldiers)

條件:
- 在掃描日(今日)往前算 3~4 個交易日內(含今日)，
  出現 3 根「漲幅超過 5%」的紅K
- 漲幅定義: (今日收盤 - 前一日收盤) / 前一日收盤 > 5%
- 紅K定義: 台股慣例以「收盤 vs 前一日收盤」決定當日紅/黑，
  故收盤 > 前一日收盤 即為紅K (與漲幅>5%條件同源，
  即使當日開盤=收盤=一字漲停也視為紅K)

回傳的 marks 包含: 所有符合條件的紅K日期
"""
from .base import SignalContext, SignalResult, register_signal

WINDOW_DAYS = 4        # 含今日往前算 4 個交易日的視窗
REQUIRED_HITS = 3      # 視窗內需要出現的根數
PCT_THRESHOLD = 5.0    # 漲幅門檻 (%)


@register_signal(
    key="three_white_soldiers",
    label="三白兵",
    description="3~4個交易日內(含今日)出現3根漲幅超過5%的紅K"
)
def check_three_white_soldiers(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()

    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")

    idx = dates.index(ctx.scan_date)
    if idx < 1:
        return SignalResult(hit=False, detail="資料不足，無法計算漲幅")

    window_start = max(1, idx - (WINDOW_DAYS - 1))

    hit_dates = []
    detail_lines = []
    for i in range(window_start, idx + 1):
        cur = df.loc[dates[i]]
        prev_close = df.loc[dates[i - 1], "Close"]
        if prev_close == 0:
            continue
        pct = (cur["Close"] - prev_close) / prev_close * 100
        is_red = cur["Close"] > prev_close  # 收盤 > 前一日收盤 即為紅K (台股慣例)
        if is_red and pct > PCT_THRESHOLD:
            hit_dates.append(dates[i])
            detail_lines.append(f"{dates[i]}(漲幅{pct:.2f}%)")

    if len(hit_dates) >= REQUIRED_HITS:
        return SignalResult(
            hit=True,
            detail=f"視窗內共 {len(hit_dates)} 根漲幅>5%紅K: {', '.join(detail_lines)} => 三白兵成立",
            marks=hit_dates,
        )

    return SignalResult(
        hit=False,
        detail=f"近{WINDOW_DAYS}個交易日內僅找到 {len(hit_dates)} 根符合條件的紅K (需要{REQUIRED_HITS}根)",
    )
