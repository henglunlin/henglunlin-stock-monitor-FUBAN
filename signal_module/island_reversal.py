"""
島狀反轉 (Island Reversal)

條件:
- 在掃描日(今日)以前的 15 個交易日內，曾出現「跳空向下」
  (某日高點 High < 前一日低點 Low)
- 掃描日當天出現「跳空向上」
  (今日低點 Low > 前一日高點 High)
- [新增條件] 掃描日前 5 個交易日內，不可出現過「跳空向上」，以確保孤島的完整性
=> 兩次跳空之間夾住的區間形成孤島，構成島狀反轉

回傳的 marks 包含: [最近一次跳空向下的日期, 掃描日期]
"""
from .base import SignalContext, SignalResult, register_signal

LOOKBACK_DAYS = 15
EXCLUDE_GAP_UP_DAYS = 5


@register_signal(
    key="island_reversal",
    label="島狀反轉",
    description="15日內先跳空向下，今日跳空向上，且前5日內無向上跳空",
)
def check_island_reversal(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()

    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")

    idx = dates.index(ctx.scan_date)
    if idx == 0:
        return SignalResult(hit=False, detail="掃描日為資料首日，無前一日可比較")

    today = df.loc[dates[idx]]
    prev = df.loc[dates[idx - 1]]

    # 1. 掃描日是否跳空向上
    gap_up_today = today["Low"] > prev["High"]
    if not gap_up_today:
        return SignalResult(hit=False, detail="掃描日未出現跳空向上，不成立")

    # 2. [新增邏輯] 往前 5 個交易日內，檢查是否曾出現過「向上跳空」
    exclude_start = max(1, idx - EXCLUDE_GAP_UP_DAYS)
    recent_gap_up_date = None
    for i in range(idx - 1, exclude_start - 1, -1):
        cur = df.loc[dates[i]]
        prv = df.loc[dates[i - 1]]
        if cur["Low"] > prv["High"]:
            recent_gap_up_date = dates[i]
            break
            
    # 如果前 5 天內已經有過向上跳空，則直接判定失敗，並說明原因
    if recent_gap_up_date is not None:
        return SignalResult(
            hit=False, 
            detail=f"今日雖跳空向上，但前 {EXCLUDE_GAP_UP_DAYS} 日內({recent_gap_up_date})也曾出現向上跳空，排除島狀反轉"
        )

    # 3. 往前 15 個交易日內尋找跳空向下 (由掃描日往回找，取「最近」一次)
    lookback_start = max(1, idx - LOOKBACK_DAYS)
    gap_down_date = None
    for i in range(idx - 1, lookback_start - 1, -1):
        cur = df.loc[dates[i]]
        prv = df.loc[dates[i - 1]]
        if cur["High"] < prv["Low"]:
            gap_down_date = dates[i]
            break

    if gap_down_date is None:
        return SignalResult(
            hit=False,
            detail=f"掃描日({ctx.scan_date})雖跳空向上，但前 {LOOKBACK_DAYS} 個交易日內未找到跳空向下，不成立"
        )

    return SignalResult(
        hit=True,
        detail=(
            f"{gap_down_date} 出現跳空向下(高點 < 前日低點)，"
            f"{ctx.scan_date} 出現跳空向上(低點 > 前日高點)，中間區間形成孤島 => 島狀反轉成立"
        ),
        marks=[gap_down_date, ctx.scan_date],
    )
