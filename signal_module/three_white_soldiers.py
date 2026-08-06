"""
三白兵 (Three White Soldiers)

條件:
- 掃描日(今日)本身必須是「漲幅超過 5%」的紅K:
  (今日收盤 - 昨日收盤) / 昨日收盤 > 5%，且收盤 > 昨收
  (今日不符合就直接不成立，不會再往前抓過去的舊紅K來湊)
- 且往前算 3~4 個交易日內(含今日)，總共出現 3 根符合上述條件的紅K
- 紅K定義: 台股慣例以「收盤 vs 前一日收盤」決定當日紅/黑，
  故收盤 > 前一日收盤 即為紅K (與漲幅>5%條件同源，
  即使當日開盤=收盤=一字漲停也視為紅K)

(改版: 原本只要求「視窗內湊到3根」，不要求今天本身也是強紅K，
 導致股價已經開始回落轉弱的今天，仍可能因為前3~4天曾經噴出過
 而被誤判成立。現在改成「今天必須先自己符合強紅K」才會繼續判斷。)

回傳的 marks 包含: 所有符合條件的紅K日期
"""
from .base import SignalContext, SignalResult, register_signal

WINDOW_DAYS = 4        # 含今日往前算 4 個交易日的視窗
REQUIRED_HITS = 3      # 視窗內需要出現的根數
PCT_THRESHOLD = 5.0    # 漲幅門檻 (%)


def _pct_change(df, dates, i):
    cur_close = df.loc[dates[i], "Close"]
    prev_close = df.loc[dates[i - 1], "Close"]
    if prev_close == 0:
        return None
    return (cur_close - prev_close) / prev_close * 100


def _is_strong_red(df, dates, i) -> bool:
    if i == 0:
        return False
    pct = _pct_change(df, dates, i)
    if pct is None:
        return False
    is_red = df.loc[dates[i], "Close"] > df.loc[dates[i - 1], "Close"]  # 收盤 > 前一日收盤 即為紅K
    return is_red and pct > PCT_THRESHOLD


@register_signal(
    key="three_white_soldiers",
    label="三白兵",
    description="今日須為漲幅超過5%的紅K，且3~4個交易日內(含今日)共出現3根這樣的紅K",
)
def check_three_white_soldiers(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()

    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")

    idx = dates.index(ctx.scan_date)
    if idx < 1:
        return SignalResult(hit=False, detail="資料不足，無法計算漲幅")

    # 今日必須本身就是強紅K，否則不成立（避免今天已經轉弱、卻抓到前幾天的舊訊號）
    if not _is_strong_red(df, dates, idx):
        pct_today = _pct_change(df, dates, idx)
        pct_text = f"{pct_today:.2f}%" if pct_today is not None else "無法計算"
        return SignalResult(
            hit=False,
            detail=f"{ctx.scan_date} 今日漲幅 {pct_text}，未達單日>5%的強紅K標準，三白兵不成立",
        )

    window_start = max(1, idx - (WINDOW_DAYS - 1))
    hit_dates = []
    detail_lines = []
    for i in range(window_start, idx + 1):
        if _is_strong_red(df, dates, i):
            pct = _pct_change(df, dates, i)
            hit_dates.append(dates[i])
            detail_lines.append(f"{dates[i]}(漲幅{pct:.2f}%)")

    if len(hit_dates) >= REQUIRED_HITS:
        return SignalResult(
            hit=True,
            detail=(
                f"今日仍為強紅K，且近{WINDOW_DAYS}個交易日內共 {len(hit_dates)} 根漲幅>5%紅K: "
                f"{', '.join(detail_lines)} => 三白兵成立"
            ),
            marks=hit_dates,
        )

    return SignalResult(
        hit=False,
        detail=(
            f"今日雖為強紅K，但近{WINDOW_DAYS}個交易日內僅找到 {len(hit_dates)} 根符合條件的紅K "
            f"(需要{REQUIRED_HITS}根)"
        ),
    )
