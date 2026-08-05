"""
廣義下降三法 (Generalized Falling Three Methods)

條件:
- 基準K線: 黑K (綠K)
- 整理K線: 經歷 3~5 根K線，最高價允許突破基準K線最高點，但最大極限為 1.5% 內 (假突破)
- 表態K線: 今日(掃描日)為黑K，且收盤價 <= 基準K線的最低價

分類為「賣出/風險提示」訊號 (kind="sell")：屬於偏空表態型態。
"""
from .base import SignalContext, SignalResult, register_signal

# 定義最大假突破容忍幅度 (1.5%)
MAX_SPIKE_TOLERANCE = 0.015

@register_signal(
    key="generalized_falling_three",
    label="廣義下降三法",
    description="基準長黑後經歷3~5根K線整理(允許1.5%假突破)，今日收盤跌破基準K最低價",
    kind="sell",
)
def check_generalized_falling_three(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()

    # 1. 檢查掃描日是否存在
    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")

    idx = dates.index(ctx.scan_date)
    
    # 確保資料天數夠長 (至少需要: 1根基準 + 3根整理 + 1根今日 = 5根)
    if idx < 4:
        return SignalResult(hit=False, detail="資料天數不足以構成完整型態")

    today = df.iloc[idx]
    
    # 2. 今日必須是黑K (收盤 < 開盤)
    if today["Close"] >= today["Open"]:
        return SignalResult(hit=False, detail="今日非黑K(綠K)，不符合表態跌破特徵")

    # 3. 動態尋找基準黑K (回溯尋找中間隔了 3~5 根的情況)
    for n_middle in [3, 4, 5]:
        ref_idx = idx - n_middle - 1
        
        # 避免索引超出範圍
        if ref_idx < 0:
            continue

        ref_k = df.iloc[ref_idx]

        # 條件 A: 基準K線必須是黑K
        if ref_k["Close"] >= ref_k["Open"]:
            continue

        # 條件 B: 今日收盤價必須「小於或等於」基準K線的最低價
        if today["Close"] > ref_k["Low"]:
            continue

        # 條件 C: 檢查中間的 3~5 根整理 K 線
        middle_valid = True
        
        # 計算容許的最高防守上限 (基準高點往上 1.5%)
        max_allowed_high = ref_k["High"] * (1 + MAX_SPIKE_TOLERANCE)
        
        # 記錄整理期間的實際最高價，方便呈現在結果中
        actual_max_high = -float('inf')

        for m_idx in range(ref_idx + 1, idx):
            mk = df.iloc[m_idx]
            actual_max_high = max(actual_max_high, mk["High"])
            
            # 若突破容許上限，型態破壞，直接跳出換下一個天數測試
            if mk["High"] > max_allowed_high:
                middle_valid = False
                break

        # 如果中間整理區間符合條件，則型態完全成立！
        if middle_valid:
            marks = [dates[ref_idx]] + [dates[m] for m in range(ref_idx + 1, idx)] + [dates[idx]]
            
            # 為了讓結果更直觀，判斷是否有觸發假突破來顯示不同的文字
            if actual_max_high > ref_k["High"]:
                spike_pct = (actual_max_high - ref_k["High"]) / ref_k["High"] * 100
                def_detail = f"假突破 {spike_pct:.2f}% (於容許1.5%內)"
            else:
                def_detail = f"未突破基準高點"
            
            return SignalResult(
                hit=True,
                detail=(
                    f"{dates[idx]} 廣義下降三法成立: "
                    f"基準黑K({dates[ref_idx]})高點 {ref_k['High']:.2f}、低點 {ref_k['Low']:.2f}，"
                    f"經歷 {n_middle} 根K線反彈整理 ({def_detail})，"
                    f"今日收盤({today['Close']:.2f})成功跌破基準低點"
                ),
                marks=marks,
            )

    return SignalResult(
        hit=False,
        detail="往前尋找 3~5 根整理區間，未發現符合「基準長黑 + 容錯1.5%內防守 + 今日黑K跌破基準低點」之型態",
    )
