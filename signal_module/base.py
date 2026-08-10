"""
訊號模組基礎架構
所有訊號模組都應該:
1. from signal_module.base import SignalContext, SignalResult, register_signal
2. 用 @register_signal(key, label, description, kind="buy"/"sell") 裝飾一個函式
3. 函式簽名: def fn(ctx: SignalContext) -> SignalResult

kind 說明:
- "buy"  : 買進/偏多訊號 (預設)
- "sell" : 賣出、出場、風險提示訊號 (例如跌停、移動停利、廣義下降三法、反向島狀)
"""
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd

# 全域訊號註冊表: { key: {"label": str, "description": str, "kind": str, "func": callable} }
SIGNAL_REGISTRY = {}


@dataclass
class SignalContext:
    """傳遞給每個訊號判斷函式的上下文"""
    code: str                  # 股票代碼
    name: str                  # 股票名稱
    df: pd.DataFrame           # 該股票完整 OHLCV 資料 (index=Date字串, 由舊到新排序), columns: Open High Low Close Volume (可能已含技術指標欄位)
    scan_date: str             # 掃描日期 (YYYY-MM-DD)，訊號判斷是否成立以此日為基準
    params: dict = field(default_factory=dict)  # 動態參數 (例如漲幅達標門檻)，由呼叫端在建立 ctx 時傳入，訊號模組可選擇性讀取


@dataclass
class SignalResult:
    """訊號判斷結果"""
    hit: bool                          # 是否觸發訊號
    detail: str = ""                   # 說明文字
    marks: list = field(default_factory=list)   # 需要在圖上標記的日期清單 (YYYY-MM-DD)
    sub_label: str = ""                # 選用: 動態附加在訊號名稱後面的短字串 (例如 "(短期)")，
                                        # 用於「同一個訊號、但這次觸發的細分類會變動」的情況
                                        # (例如下降趨勢線突破依觸發當下區分短期/中短期/中長期)，
                                        # 讓「訊號類型」欄位不用因此拆成好幾個獨立註冊的訊號。
                                        # 大多數訊號不需要設定這個欄位，維持預設空字串即可。


def register_signal(key: str, label: str, description: str = "", kind: str = "buy"):
    """裝飾器: 註冊一個訊號判斷函式"""
    def deco(func):
        SIGNAL_REGISTRY[key] = {
            "label": label,
            "description": description,
            "kind": kind,
            "func": func,
        }
        return func
    return deco
