"""
布林通道縮窄突破 (Bollinger Bands Squeeze Breakout)

條件 (以掃描日為基準):
1. 前兩日內 (昨日或前日) 至少有一日的布林通道帶寬 (Bandwidth) 收斂至 12% 以下 (極度平靜)。
2. 掃描日當天收盤為紅K，且收盤價突破布林通道「上軌」。
3. 掃描日成交量大於前5日均量 1.5 倍 (帶量發動)。
4. 依據是否留有跳空缺口，顯示 "Band 跳空突破" 或 "Band 突破"。

需要 SignalContext.df 已包含 BB_UB / BB_BW 欄位 (見 indicators.add_indicators)
"""
import pandas as pd
from .base import SignalContext, SignalResult, register_signal

# 參數設定
MAX_BANDWIDTH = 12.0  # 通道縮窄的極限值 (%)
VOL_MULTIPLIER = 1.5  # 爆量倍數
VOL_MA_PERIOD = 5     # 均量天數改為 5 日

@register_signal(
    key="bband_breakout",
    label="布林縮窄突破",
    description="前兩日內布林帶寬曾縮至12%內後，帶量(5MA)突破上軌",
)
def check_bband_breakout(ctx: SignalContext) -> SignalResult:
    df = ctx.df
    dates = df.index.tolist()

    if ctx.scan_date not in dates:
        return SignalResult(hit=False, detail="掃描日不在資料範圍內")
        
    # 確保所需指標欄位已由 indicators.py 產生
    if "BB_UB" not in df.columns or "BB_BW" not in df.columns:
        return SignalResult(hit=False, detail="資料缺少 BB_UB / BB_BW 指標欄位")

    idx = dates.index(ctx.scan_date)
    
    # 確保有前兩日的資料可以觀察帶寬收斂狀況
    if idx < 2:
        return SignalResult(hit=False, detail="資料不足，無法取得前兩日狀態")

    # 取得前兩日與今日的數值
    prev_bw = df.iloc[idx - 1]["BB_BW"]
    prev2_bw = df.iloc[idx - 2]["BB_BW"]
    today_ub = df.iloc[idx]["BB_UB"]
    today_close = df.iloc[idx]["Close"]
    today_open = df.iloc[idx]["Open"]
    today_vol = df.iloc[idx]["Volume"]
    today_low = df.iloc[idx]["Low"]
    
    # 若計算出的帶寬為空值(例如前幾筆資料)，防呆跳出
    if pd.isna(prev_bw) or pd.isna(prev2_bw) or pd.isna(today_ub):
        return SignalResult(hit=False, detail="布林通道資料不足(含空值)，無法判定")
    
    # 取得前一日的最高價與收盤價，用於判斷跳空
    prev_high = df.iloc[idx - 1]["High"]
    prev_close = df.iloc[idx - 1]["Close"]
    
    # 計算前 5 日均量 (不含今日)
    prev_vol_avg = df["Volume"].iloc[max(0, idx - VOL_MA_PERIOD):idx].mean()

    # 條件 1: 前兩日是否有任一日帶寬縮窄至 12% 內
    if prev_bw > MAX_BANDWIDTH and prev2_bw > MAX_BANDWIDTH:
        return SignalResult(
            hit=False, 
            detail=f"{ctx.scan_date} 前兩日帶寬(昨:{prev_bw:.2f}%, 前:{prev2_bw:.2f}%)皆未達收斂標準(<= {MAX_BANDWIDTH}%)"
        )
        
    # 條件 2: 是否紅K且突破上軌
    if today_close <= today_open or today_close <= today_ub:
        return SignalResult(
            hit=False, 
            detail=f"{ctx.scan_date} 收盤價({today_close})未能以紅K突破上軌({today_ub:.2f})"
        )
        
    # 條件 3: 是否帶量 (5日均量 1.5倍)
    threshold_vol = prev_vol_avg * VOL_MULTIPLIER
    if today_vol < threshold_vol:
        return SignalResult(
            hit=False, 
            detail=f"{ctx.scan_date} 成交量({today_vol:.0f})未達前5日均量1.5倍({threshold_vol:.0f})"
        )

    # 判定是否為「跳空」 (開盤大於昨收，且最低價大於昨日最高價留下缺口)
    is_gap_up = (today_open > prev_close) and (today_low > prev_high)
    signal_label = "Band 跳空突破" if is_gap_up else "Band 突破"

    # 取符合標準的最小帶寬顯示，讓使用者知道極度收斂到多少
    min_bw = min(prev_bw, prev2_bw)

    # 全部條件吻合
    return SignalResult(
        hit=True,
        detail=(
            f"{ctx.scan_date} 前兩日內布林帶寬曾縮至 {min_bw:.2f}%，"
            f"當日帶量({today_vol:.0f})紅K突破上軌({today_ub:.2f}) => {signal_label}成立！"
        ),
        marks=[ctx.scan_date]
    )
