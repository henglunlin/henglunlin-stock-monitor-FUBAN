"""
技術指標計算工具: KD(9,3,3)、各天期均線(MA5/10/20/60)、成交量10日均量、乖離率(Bias)、布林通道(BBand)
"""
import numpy as np
import pandas as pd


def compute_kd(df: pd.DataFrame, n: int = 9, k_smooth: int = 3, d_smooth: int = 3, seed: float = 50.0):
    """
    計算 KD 指標 (標準 9,3,3 平滑法)
    RSV = (Close - N日內最低) / (N日內最高 - N日內最低) * 100
    K = 前日K * (2/3) + 今日RSV * (1/3)
    D = 前日D * (2/3) + 今日K * (1/3)
    起始 K=D=seed(預設50)
    """
    low_n = df["Low"].rolling(n, min_periods=1).min()
    high_n = df["High"].rolling(n, min_periods=1).max()
    rng = (high_n - low_n).replace(0, np.nan)
    rsv = (df["Close"] - low_n) / rng * 100
    rsv = rsv.fillna(50)

    k_alpha = 1.0 / k_smooth
    d_alpha = 1.0 / d_smooth

    K, D = [], []
    prev_k, prev_d = seed, seed
    for v in rsv:
        k = prev_k * (1 - k_alpha) + v * k_alpha
        d = prev_d * (1 - d_alpha) + k * d_alpha
        K.append(k)
        D.append(d)
        prev_k, prev_d = k, d

    return pd.Series(K, index=df.index), pd.Series(D, index=df.index)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """在 df 上新增 MA5/10/20/60、乖離率(20MA/60MA)、VolMA10、K/D、布林通道與帶寬 欄位 (回傳新的 DataFrame)"""
    df = df.copy()
    
    # 1. 補上 5MA / 10MA / 20MA，並保留原有的 60MA
    df["MA5"] = df["Close"].rolling(5, min_periods=1).mean()
    df["MA10"] = df["Close"].rolling(10, min_periods=1).mean()
    df["MA20"] = df["Close"].rolling(20, min_periods=1).mean()
    df["MA60"] = df["Close"].rolling(60, min_periods=1).mean()
    
    # 2. 補上 20MA 與 60MA 的乖離率 (單位: %)
    # 公式：(收盤價 - 均線) / 均線 * 100
    df["Bias20"] = (df["Close"] - df["MA20"]) / df["MA20"] * 100
    df["Bias60"] = (df["Close"] - df["MA60"]) / df["MA60"] * 100
    
    # 3. 原有的成交量均量與 KD 指標
    df["VolMA10"] = df["Volume"].rolling(10, min_periods=1).mean()
    K, D = compute_kd(df)
    df["K"] = K
    df["D"] = D
    
    # 4. 新增計算布林通道 (20MA, 2 std) 及帶寬
    df["BB_std"] = df["Close"].rolling(20, min_periods=1).std()
    df["BB_UB"] = df["MA20"] + 2 * df["BB_std"]
    df["BB_LB"] = df["MA20"] - 2 * df["BB_std"]
    # 計算帶寬 (以百分比表示)
    df["BB_BW"] = (df["BB_UB"] - df["BB_LB"]) / df["MA20"] * 100
    
    return df
