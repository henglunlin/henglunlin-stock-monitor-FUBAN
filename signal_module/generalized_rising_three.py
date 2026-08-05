"""
廣義上升三法 (Generalized Rising Three Methods)

條件:
- 基準K線: 紅K
- 整理K線: 經歷 3~5 根K線，最低價允許跌破基準K線最低點，但最大極限為 1.5% 內 (假跌破)
- 表態K線: 今日(掃描日)為紅K，且收盤價 >= 基準K線的最高價
"""
from .base import SignalContext, SignalResult, register_signal

# 定義最大假跌破容忍幅度 (1.5%)
MAX_DRAWDOWN_TOLERANCE = 0.015

@register_signal(
    key="generalized_rising_three",
    label="廣義上升三法",
    description="基準長紅後經歷3~4根K線整理(允許1.5%假跌破)，今日收盤突破基準K最高價",
)
def check_generalized_rising_three(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()

    # 1. 檢查掃描日是否存在
    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")

    idx = dates.index(ctx.scan_date)
    
    # 確保資料天數夠長 (至少需要: 1根基準 + 4根整理 + 1根今日 = 6根)
    if idx < 5:
        return SignalResult(hit=False, detail="資料天數不足以構成完整型態")

    today = df.iloc[idx]
    
    # 2. 今日必須是紅K (收盤 > 開盤)
    if today["Close"] <= today["Open"]:
        return SignalResult(hit=False, detail="今日非紅K，不符合表態突破特徵")

    # 3. 動態尋找基準紅K (回溯尋找中間隔了 3~4 根的情況)
    for n_middle in [3, 5]:
        ref_idx = idx - n_middle - 1
        
        # 避免索引超出範圍
        if ref_idx < 0:
            continue

        ref_k = df.iloc[ref_idx]

        # 條件 A: 基準K線必須是紅K
        if ref_k["Close"] <= ref_k["Open"]:
            continue

        # 條件 B: 今日收盤價必須「大於或等於」基準K線的最高價
        if today["Close"] < ref_k["High"]:
            continue

        # 條件 C: 檢查中間的 3~4 根整理 K 線
        middle_valid = True
        
        # 計算容許的最低防守底線 (基準低點往下 1.5%)
        min_allowed_low = ref_k["Low"] * (1 - MAX_DRAWDOWN_TOLERANCE)
        
        # 記錄整理期間的實際最低價，方便呈現在結果中
        actual_min_low = float('inf')

        for m_idx in range(ref_idx + 1, idx):
            mk = df.iloc[m_idx]
            actual_min_low = min(actual_min_low, mk["Low"])
            
            # 若跌破容許底線，型態破壞，直接跳出換下一個天數測試
            if mk["Low"] < min_allowed_low:
                middle_valid = False
                break

        # 如果中間整理區間符合條件，則型態完全成立！
        if middle_valid:
            marks = [dates[ref_idx]] + [dates[m] for m in range(ref_idx + 1, idx)] + [dates[idx]]
            
            # 為了讓結果更直觀，判斷是否有觸發假跌破來顯示不同的文字
            if actual_min_low < ref_k["Low"]:
                drawdown_pct = (ref_k["Low"] - actual_min_low) / ref_k["Low"] * 100
                def_detail = f"假跌破 {drawdown_pct:.2f}% (於容許1.5%內)"
            else:
                def_detail = f"未跌破基準低點"
            
            return SignalResult(
                hit=True,
                detail=(
                    f"{dates[idx]} 廣義上升三法成立: "
                    f"基準紅K({dates[ref_idx]})高點 {ref_k['High']:.2f}、低點 {ref_k['Low']:.2f}，"
                    f"經歷 {n_middle} 根K線洗盤 ({def_detail})，"
                    f"今日收盤({today['Close']:.2f})成功站上基準高點"
                ),
                marks=marks,
            )

    return SignalResult(
        hit=False,
        detail="往前尋找 3~5 根整理區間，未發現符合「基準長紅 + 容錯1.5%內防守 + 今日紅K站上基準高點」之型態",
    )
