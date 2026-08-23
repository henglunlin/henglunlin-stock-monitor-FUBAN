"""
買入區間 / 停損價目標價訊號模組
==================================
跟其他訊號模組不同的地方：這裡判斷的不是「技術指標算出來的型態」，而是使用者自己在
「🎯 目標價編輯」頁面替每一檔股票設定的目標價（存在 target_price_list.json）。

monitor 主程式 app.py 的 run_stock_signals() 每次呼叫時，會先幫「這一檔股票」從
target_price_list.json 查出對應的設定，塞進 ctx.params["target_price"]，
格式如下（沒有設定目標價的股票，這個 key 會是 None 或整個不存在）：

    {
        "target_price": float,        # 目標買入價格（中心價）
        "low_pct": float,             # 買入價格(L)%，預設 5
        "high_pct": float,            # 買入價格(U)%，預設 5
        "stop_loss": float | None,    # 停損價格，可為 None（沒設定停損）
    }

買入區間 = [目標買入價格 × (1 - 買入價格(L)% / 100), 目標買入價格 × (1 + 買入價格(U)% / 100)]
（跟 target_price_list.xlsx 裡 E/F 兩欄的公式一致）

這裡註冊兩個訊號：
- target_price_buy_zone：目前價格落在買入區間內 -> 買進方向
- target_price_stop_loss：目前價格已跌破（觸及或跌破）停損價 -> 賣出/風險方向

label 特意取跟 monitor 主迴圈組 Telegram 彙整訊息時同一組字，
方便「買賣訊號」欄位、Telegram 訊息兩邊看到的用詞一致：「進入買入區間」「觸及停損價格」。
"""
import pandas as pd

from .base import SignalContext, SignalResult, register_signal


def _get_target_price_config(ctx: SignalContext):
    """從 ctx.params 取出這檔股票的目標價設定；沒有設定或格式不對就回傳 None。"""
    cfg = ctx.params.get("target_price") if ctx.params else None
    if not isinstance(cfg, dict):
        return None
    center = cfg.get("target_price")
    if center is None:
        return None
    try:
        center = float(center)
    except (TypeError, ValueError):
        return None
    if center <= 0:
        return None
    return cfg


def _is_enabled(cfg: dict) -> bool:
    """個股開關：預設開（True）。只有明確存 False（或等價值）才視為關閉，
    這樣舊資料（沒有 enabled 這個 key 的既有 target_price_list.json）不會被誤判成關閉。
    """
    return cfg.get("enabled", True) is not False


def _current_close(ctx: SignalContext):
    """取得 scan_date 這一天的收盤價（已經是「今天」即時價格併入後的資料）。"""
    df = ctx.df
    if ctx.scan_date not in df.index:
        return None
    try:
        close = df.loc[ctx.scan_date, "Close"]
    except Exception:
        return None
    if close is None:
        return None
    try:
        if pd.isna(close):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(close)
    except (TypeError, ValueError):
        return None


def compute_buy_zone(center: float, low_pct: float, high_pct: float):
    """回傳 (lower, upper)，跟 target_price_list.xlsx 的 E/F 欄公式一致。"""
    lower = center * (1 - low_pct / 100)
    upper = center * (1 + high_pct / 100)
    return lower, upper


@register_signal(
    key="target_price_buy_zone",
    label="進入買入區間",
    description="目前價格落在自訂的買入目標價區間內（中心價 ± 各自設定的百分比，於「🎯 目標價編輯」頁面設定）",
    kind="buy",
)
def check_target_price_buy_zone(ctx: SignalContext) -> SignalResult:
    cfg = _get_target_price_config(ctx)
    if cfg is None:
        return SignalResult(hit=False, detail="未設定目標價")
    if not _is_enabled(cfg):
        return SignalResult(hit=False, detail="此股票的目標價提醒已停用（開關關閉）")

    price = _current_close(ctx)
    if price is None:
        return SignalResult(hit=False, detail="無法取得目前價格")

    center = float(cfg.get("target_price"))
    low_pct = float(cfg.get("low_pct", 5) or 5)
    high_pct = float(cfg.get("high_pct", 5) or 5)
    lower, upper = compute_buy_zone(center, low_pct, high_pct)

    if lower <= price <= upper:
        return SignalResult(
            hit=True,
            detail=f"價格 {price:.2f} 落在買入區間 {lower:.2f}-{upper:.2f}（目標買入價 {center:.2f}）",
        )
    return SignalResult(
        hit=False,
        detail=f"價格 {price:.2f} 不在買入區間 {lower:.2f}-{upper:.2f} 內",
    )


@register_signal(
    key="target_price_stop_loss",
    label="觸及停損價格",
    description="目前價格已觸及或跌破自訂的停損價格（於「🎯 目標價編輯」頁面設定）",
    kind="sell",
)
def check_target_price_stop_loss(ctx: SignalContext) -> SignalResult:
    cfg = _get_target_price_config(ctx)
    if cfg is None:
        return SignalResult(hit=False, detail="未設定目標價")
    if not _is_enabled(cfg):
        return SignalResult(hit=False, detail="此股票的目標價提醒已停用（開關關閉）")

    stop_loss = cfg.get("stop_loss")
    if stop_loss is None:
        return SignalResult(hit=False, detail="未設定停損價格")
    try:
        stop_loss = float(stop_loss)
    except (TypeError, ValueError):
        return SignalResult(hit=False, detail="停損價格格式錯誤")
    if stop_loss <= 0:
        return SignalResult(hit=False, detail="未設定停損價格")

    price = _current_close(ctx)
    if price is None:
        return SignalResult(hit=False, detail="無法取得目前價格")

    if price <= stop_loss:
        return SignalResult(
            hit=True,
            detail=f"價格 {price:.2f} 已觸及停損價格 {stop_loss:.2f}",
        )
    return SignalResult(
        hit=False,
        detail=f"價格 {price:.2f} 尚未觸及停損價格 {stop_loss:.2f}",
    )
