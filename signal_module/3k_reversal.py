"""
3K反轉 (3-Candle Reversal)

條件:
- 價格條件: 今日收盤價 > 昨日高點 且 今日收盤價 > 前日收盤價
- 昨收條件: 昨日收盤價 <= 前日最高價
- K線型態: 今日以前的兩根K線 (昨日與前日)，最少有一根為黑K (收盤 < 開盤)
- 均線條件: 今日收盤價 > 60MA
- 乖離條件: 今日收盤價與 20MA 或 60MA 乖離率 (其中之一) 在 10% 以內
"""
from .base import SignalContext, SignalResult, register_signal

# 乖離率參數設定
BIAS_MAX_THRESHOLD = 10.0

@register_signal(
    key="3k_reversal",
    label="3K反轉",
    description="今收>昨高且今收>前收，昨收<=前高，前兩日至少一黑K，站上60MA，20/60MA乖離10%內",
)
def check_3k_reversal(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()

    # 1. 檢查掃描日是否存在
    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")

    idx = dates.index(ctx.scan_date)

    # 2. 確保有足夠的資料天數 (至少需要今日、昨日、前日共3天)
    if idx < 2:
        return SignalResult(hit=False, detail="資料天數不足，無法比對前兩日資料")

    # 3. 確保資料表中含有我們需要的欄位
    required_cols = ["MA20", "MA60", "Bias20", "Bias60"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        return SignalResult(hit=False, detail=f"資料缺少欄位: {', '.join(missing)}")

    # 取出這三天的 K 線資料
    today = df.iloc[idx]
    yest = df.iloc[idx - 1]
    prev2 = df.iloc[idx - 2]
    
    # 提取需要的價位與指標
    c0 = today["Close"]
    h1 = yest["High"]
    c1 = yest["Close"]
    c2 = prev2["Close"]
    h2 = prev2["High"]
    
    ma60 = today["MA60"]
    bias20 = today["Bias20"]
    bias60 = today["Bias60"]

    # ---------------- 邏輯判斷區塊 ----------------
    # 條件 1: 今日收盤價 > 昨日高點 且 今日收盤價 > 前日收盤
    cond_price = (c0 > h1) and (c0 > c2)
    
    # 條件 2: 今日收盤價 > 60MA
    cond_ma = c0 > ma60
    
    # 條件 3: 前兩根K線(昨日與前日)最少有一根為黑K (收盤 < 開盤)
    is_black_yest = yest["Close"] < yest["Open"]
    is_black_prev2 = prev2["Close"] < prev2["Open"]
    cond_black = is_black_yest or is_black_prev2

    # 條件 4: 乖離率判斷 (20MA 或 60MA 乖離率 <= 10%)
    cond_bias = (bias20 <= BIAS_MAX_THRESHOLD) or (bias60 <= BIAS_MAX_THRESHOLD)
    
    # 條件 5: 昨日收盤價 <= 前日最高價
    cond_yest_close = (c1 <= h2)
    # ----------------------------------------------

    # 條件全數成立
    if cond_price and cond_ma and cond_black and cond_bias and cond_yest_close:
        # 判斷是哪條均線滿足乖離條件，方便顯示在介面上
        matched_bias = []
        if bias20 <= BIAS_MAX_THRESHOLD:
            matched_bias.append(f"20MA({bias20:.2f}%)")
        if bias60 <= BIAS_MAX_THRESHOLD:
            matched_bias.append(f"60MA({bias60:.2f}%)")
            
        return SignalResult(
            hit=True,
            detail=(
                f"{ctx.scan_date} 符合3K反轉: 今收({c0}) > 昨高({h1}) 且 今收({c0}) > 前收({c2})，"
                f"昨收({c1}) <= 前高({h2})，"
                f"前兩日含黑K洗盤，站上60MA({ma60:.2f})，"
                f"符合乖離率標準: {' 且 '.join(matched_bias)} <= {BIAS_MAX_THRESHOLD}%"
            ),
            marks=[dates[idx - 2], dates[idx - 1], dates[idx]], 
        )

    # 條件不成立時，記錄具體未滿足的原因
    reasons = []
    if not cond_price:
        reasons.append(f"價格未滿足「今收({c0}) > 昨高({h1}) 且 今收({c0}) > 前收({c2})」")
    if not cond_yest_close:
        reasons.append(f"昨收({c1}) 大於 前高({h2})")
    if not cond_black:
        reasons.append(f"前兩日皆非黑K (缺乏洗盤特徵)")
    if not cond_ma:
        reasons.append(f"收盤({c0})未站上60MA({ma60:.2f})")
    if cond_ma and not cond_bias:
        reasons.append(f"20MA乖離({bias20:.2f}%) 與 60MA乖離({bias60:.2f}%) 皆大於 {BIAS_MAX_THRESHOLD}%")

    return SignalResult(
        hit=False,
        detail=f"3K反轉不成立: " + "；".join(reasons),
    )
