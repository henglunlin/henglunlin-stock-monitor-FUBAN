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

# 訊號優先等級表: { label: priority(int，越小越重要) }，以及找不到對應 label 時的預設等級。
# 實際內容由 signal_module/priority.py 負責維護 (要調整優先等級，去改 priority.py，
# 不是直接改這裡)。這兩個名字之所以放在 base.py 而不是直接放在 priority.py 裡宣告，
# 是因為 base.py 是 module_loader 的 EXCLUDE_FILES、永遠不會被「整個模組物件砍掉重造」；
# module_loader 每次重新載入 signal_module/*.py 時，其他檔案都是砍掉重造整個模組物件、
# 不是原地 reload，如果 SIGNAL_PRIORITY 這個 dict 是宣告在會被砍掉重造的 priority.py 裡，
# 監控主程式 app.py 一開始 import 進去的參照就會在下一次「儲存並重新載入」後失效(還是舊的)。
# 讓 priority.py 只對這裡的 dict 做 .clear()/.update() 「原地」更新 (物件本身不換掉)，
# app.py 才能一直讀到最新值 —— 用法跟上面 SIGNAL_REGISTRY 的原地更新是同一套邏輯。
SIGNAL_PRIORITY = {}
SIGNAL_PRIORITY_DEFAULT = 3


@dataclass
class SignalContext:
    """傳遞給每個訊號判斷函式的上下文"""
    code: str                  # 股票代碼
    name: str                  # 股票名稱
    df: pd.DataFrame           # 該股票完整 OHLCV 資料 (index=Date字串, 由舊到新排序), columns: Open High Low Close Volume (可能已含技術指標欄位)
    scan_date: str             # 掃描日期 (YYYY-MM-DD)，訊號判斷是否成立以此日為基準
    params: dict = field(default_factory=dict)  # 動態參數 (例如漲幅達標門檻)，由呼叫端在建立 ctx 時傳入，訊號模組可選擇性讀取
    # 效能優化: dates / date_to_idx 快取。
    # 同一檔股票在同一次掃描中，會用同一個 df 建立好幾個 SignalContext (每個訊號一個)，
    # 而每個訊號原本各自都要重算一次 `df.index.tolist()` 以及線性搜尋 `dates.index(scan_date)`。
    # 這裡改成: 若呼叫端沒有預先算好傳進來，才由 __post_init__ 自動計算一次；
    # 若呼叫端已經算好 (例如同一檔股票、同一個 df 要建立多個 ctx 時)，可以直接傳入重複利用，
    # 避免同一份資料被重複轉換/搜尋 N 次。
    # date_to_idx 用 dict 做 O(1) 查找，取代原本各訊號模組自己寫的 `dates.index(...)` (O(n) 線性搜尋)。
    dates: Optional[list] = None
    date_to_idx: Optional[dict] = None

    def __post_init__(self):
        if self.dates is None:
            self.dates = self.df.index.tolist()
        if self.date_to_idx is None:
            self.date_to_idx = {d: i for i, d in enumerate(self.dates)}


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
