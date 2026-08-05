"""
反向島狀 (Bearish Island Reversal)

條件:
- 在掃描日(今日)以前的 15 個交易日內，曾出現「跳空向上」
  (某日低點 Low > 前一日高點 High)
- 掃描日當天出現「跳空向下」
  (今日高點 High < 前一日低點 Low)
- [新增條件] 掃描日前 5 個交易日內，不可出現過「跳空向下」，以確保孤島的完整性
=> 兩次跳空之間夾住的區間形成孤島(頭部)，構成反向島狀

回傳的 marks 包含: [最近一次跳空向上的日期, 掃描日期]

分類為「賣出/風險提示」訊號 (kind="sell")：頭部反轉型態。
"""
from .base import SignalContext, SignalResult, register_signal

LOOKBACK_DAYS = 15
# 這裡對應更改為排除向下跳空的天數
EXCLUDE_GAP_DOWN_DAYS = 5 


@register_signal(
    key="reverse_island",
    label="反向島狀",
    description="15日內先跳空向上，今日跳空向下，且前5日內無向下跳空",
    kind="sell",
)
def check_reverse_island(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()

    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")

    idx = dates.index(ctx.scan_date)
    if idx == 0:
        return SignalResult(hit=False, detail="掃描日為資料首日，無前一日可比較")

    today = df.loc[dates[idx]]
    prev = df.loc[dates[idx - 1]]

    # 1. 掃描日是否跳空「向下」
    gap_down_today = today["High"] < prev["Low"]
    if not gap_down_today:
        return SignalResult(hit=False, detail="掃描日未出現跳空向下，不成立")

    # 2. [新增邏輯] 往前 5 個交易日內，檢查是否曾出現過「向下跳空」
    exclude_start = max(1, idx - EXCLUDE_GAP_DOWN_DAYS)
    recent_gap_down_date = None
    for i in range(idx - 1, exclude_start - 1, -1):
        cur = df.loc[dates[i]]
        prv = df.loc[dates[i - 1]]
        if cur["High"] < prv["Low"]:
            recent_gap_down_date = dates[i]
            break
            
    # 如果前 5 天內已經有過向下跳空，則直接判定失敗，並說明原因
    if recent_gap_down_date is not None:
        return SignalResult(
            hit=False, 
            detail=f"今日雖跳空向下，但前 {EXCLUDE_GAP_DOWN_DAYS} 日內({recent_gap_down_date})也曾出現向下跳空，排除反向島狀"
        )

    # 3. 往前 15 個交易日內尋找跳空「向上」 (由掃描日往回找，取「最近」一次)
    lookback_start = max(1, idx - LOOKBACK_DAYS)
    gap_up_date = None
    for i in range(idx - 1, lookback_start - 1, -1):
        cur = df.loc[dates[i]]
        prv = df.loc[dates[i - 1]]
        if cur["Low"] > prv["High"]:
            gap_up_date = dates[i]
            break

    if gap_up_date is None:
        return SignalResult(
            hit=False,
            detail=f"掃描日({ctx.scan_date})雖跳空向下，但前 {LOOKBACK_DAYS} 個交易日內未找到跳空向上，不成立"
        )

    return SignalResult(
        hit=True,
        detail=(
            f"{gap_up_date} 出現跳空向上(低點 > 前日高點)，"
            f"{ctx.scan_date} 出現跳空向下(高點 < 前日低點)，中間區間形成頂部孤島 => 反向島狀成立"
        ),
        marks=[gap_up_date, ctx.scan_date],
    )
