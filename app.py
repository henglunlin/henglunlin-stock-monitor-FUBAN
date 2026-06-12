# -*- coding: utf-8 -*-

import os
import re
import json
import copy
import time
import gc
import base64
import tempfile
import threading
import requests
from html import escape
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
import streamlit as st

# ===== 富邦 API 引入 =====
try:
    from fubon_neo.sdk import FubonSDK
except ImportError as e:
    FubonSDK = None

# ===== Streamlit UI 基本設定 =====
st.set_page_config(layout="wide")

# ===== 常數設定 =====
TW_TZ = ZoneInfo("Asia/Taipei")
REFRESH_SEC = 5  # WebSocket 即時版建議 2~5 秒刷新畫面一次
HISTORY_CACHE_TTL = 60
ENABLE_GAP_SIGNAL = True
GROUP_EDIT_PIN = "1219"
GROUPS_FILE = "stock_groups.json"
BACKUP_DIR = "backups"
STOCK_NAME_FILE = "TWstocklistname.txt"

# ===== Telegram 設定 =====
def get_secret_or_default(key: str, default: str = ""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default

TELEGRAM_BOT_TOKEN = get_secret_or_default("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = get_secret_or_default("TELEGRAM_CHAT_ID", "")

DEFAULT_STOCK_GROUPS = {
    "權值股": [
        "2330.TW", "00981A.TW", "2449.TW", "2317.TW", "3711.TW",
        "6488.TWO", "2327.TW", "6176.TW", "2303.TW", "5347.TWO",
    ],
    "自選股1": [
        "3008.TW", "3035.TW", "4566.TW", "4956.TW", "6456.TW",
        "4749.TWO", "6271.TW", "6290.TWO", "4919.TW",
    ],
    "低軌衛星": ["6285.TW", "2313.TW"],
    "ABF": ["4958.TW", "3037.TW", "8046.TW", "3189.TW", "8996.TW", "5439.TWO", "8358.TWO"],
    "記憶體": ["6770.TW", "2408.TW", "2344.TW", "8271.TW", "4967.TW", "3260.TWO", "2451.TW"],
    "CCL": ["2383.TW", "6274.TWO", "6213.TW", "8039.TW"],
    "CPO": ["4979.TWO", "3163.TWO", "4977.TW", "3081.TWO", "3450.TW", "6442.TW"],
}

# ===== CSS =====
st.markdown("""
<style>
.dashboard-scroll { overflow-x: auto; overflow-y: hidden; width: 100%; padding-bottom: 8px; }
.dashboard-grid { display: grid; grid-template-columns: repeat(4, minmax(260px, 1fr)); gap: 12px; min-width: 1120px; }
.dashboard-card { border-radius: 12px; padding: 14px 16px; min-height: 180px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); box-sizing: border-box; }
.dashboard-title { font-size: 18px; font-weight: 700; margin-bottom: 10px; color: #000000 !important; }
.dashboard-main { font-size: 28px; font-weight: 800; margin-bottom: 6px; }
.dashboard-sub { font-size: 14px; color: #000000 !important; margin-bottom: 10px; }
.dashboard-detail { font-size: 14px; line-height: 1.7; color: #000000 !important; }
.dashboard-extra { font-size: 13px; line-height: 1.6; color: #000000 !important; margin-top: 10px; padding-top: 8px; border-top: 1px solid rgba(0,0,0,0.12); word-break: break-word; }
.dashboard-link, .dashboard-link:link, .dashboard-link:visited, .dashboard-link:hover, .dashboard-link:active { text-decoration: none !important; color: inherit !important; }
.back-to-dashboard-btn { display: inline-block; padding: 6px 12px; border-radius: 8px; border: 1px solid #999; background: #f5f5f5; color: #000 !important; text-decoration: none !important; font-size: 14px; font-weight: 600; text-align: center; }
.back-to-dashboard-btn:hover { background: #eaeaea; }
.ws-ok { color:#389e0d; font-weight:700; }
.ws-bad { color:#cf1322; font-weight:700; }
</style>
""", unsafe_allow_html=True)

# =============================================================================
# 基礎工具
# =============================================================================
def symbol_to_code(symbol: str) -> str:
    return str(symbol).strip().upper().split(".")[0]


def yahoo_quote_url(symbol: str) -> str:
    return f"https://tw.stock.yahoo.com/quote/{symbol_to_code(symbol)}"


def make_anchor_id(group_name: str) -> str:
    anchor = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", group_name).strip("-")
    return f"group-{anchor}"


def normalize_symbol_quick(input_text: str):
    s = str(input_text).strip().upper()
    if not s:
        return None
    if "." in s:
        return s
    if re.match(r"^\d{4,6}[A-Z]?$", s):
        if s.startswith(("3", "6", "8")):
            return f"{s}.TWO"
        return f"{s}.TW"
    return s


def normalize_symbols_from_text(text: str):
    if not text:
        return []
    text = text.replace("，", ",")
    items = []
    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        items.extend([p.strip().upper() for p in raw_line.split(",") if p.strip()])

    seen, result = set(), []
    for s in items:
        normalized = normalize_symbol_quick(s)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def compact_name_list(names, max_show=3):
    names = [str(x).strip() for x in names if str(x).strip()]
    if not names:
        return "無"
    if len(names) <= max_show:
        return "、".join(names)
    return "、".join(names[:max_show]) + f" 等{len(names)}檔"

# =============================================================================
# 分組讀寫
# =============================================================================
def load_stock_groups():
    if os.path.exists(GROUPS_FILE):
        try:
            with open(GROUPS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                return data
        except Exception:
            pass
    return copy.deepcopy(DEFAULT_STOCK_GROUPS)


def save_stock_groups(groups):
    with open(GROUPS_FILE, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)


def ensure_backup_dir():
    os.makedirs(BACKUP_DIR, exist_ok=True)


def create_backup_filename():
    tw_now = datetime.now(TW_TZ)
    return f"stock_groups_backup_{tw_now.strftime('%Y%m%d_%H%M%S')}.json"


def save_backup_snapshot(groups):
    ensure_backup_dir()
    file_path = os.path.join(BACKUP_DIR, create_backup_filename())
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)
    return file_path


def list_backup_files():
    if not os.path.exists(BACKUP_DIR):
        return []
    files = []
    for name in os.listdir(BACKUP_DIR):
        if name.lower().endswith(".json"):
            full_path = os.path.join(BACKUP_DIR, name)
            if os.path.isfile(full_path):
                files.append((name, os.path.getmtime(full_path)))
    files.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in files]


def validate_and_normalize_group_json(data):
    if not isinstance(data, dict) or not data:
        raise ValueError("JSON 格式錯誤：最外層必須是非空物件（dict）")
    validated = {}
    for group_name, symbols in data.items():
        group_name = str(group_name).strip()
        if not group_name:
            raise ValueError("JSON 格式錯誤：分類名稱不可為空")
        if isinstance(symbols, list):
            raw_text = "\n".join(str(x) for x in symbols)
        elif isinstance(symbols, str):
            raw_text = symbols
        else:
            raise ValueError(f"JSON 格式錯誤：分類「{group_name}」的股票清單必須是 list 或 string")
        validated[group_name] = normalize_symbols_from_text(raw_text)
    return validated

# =============================================================================
# Telegram
# =============================================================================
def send_telegram_message(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        res = requests.post(url, json=payload, timeout=5)
        if res.status_code != 200:
            st.error(f"Telegram 傳送失敗，API 回傳：{res.text}")
    except Exception as e:
        st.error(f"Telegram 連線失敗: {e}")


def check_telegram_push_command():
    if not TELEGRAM_BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"timeout": 1}
    if st.session_state.get("tg_last_update_id"):
        params["offset"] = st.session_state.tg_last_update_id + 1
    try:
        res = requests.get(url, params=params, timeout=3)
        if res.status_code == 200:
            data = res.json()
            if data.get("ok") and data.get("result"):
                triggered = False
                for item in data["result"]:
                    st.session_state.tg_last_update_id = item["update_id"]
                    message_text = item.get("message", {}).get("text", "").strip().lower()
                    if message_text == "push":
                        triggered = True
                return triggered
    except Exception:
        pass
    return False

# =============================================================================
# 富邦 WebSocket 管理器：這是本版核心
# =============================================================================
class FubonRealtimeManager:
    def __init__(self):
        self.sdk = None
        self.ws = None
        self.lock = threading.RLock()
        self.logged_in = False
        self.connected = False
        self.error = None
        self.prices = {}
        self.messages = {}
        self.subscribed = set()
        self.last_message_at = None
        self.cert_path = None

    def login(self, fubon_id: str, fubon_password: str, cert_password: str, pfx_base64: str):
        if FubonSDK is None:
            raise RuntimeError("富邦 SDK 尚未安裝或載入失敗")

        cert_bytes = base64.b64decode(pfx_base64)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pfx")
        tmp.write(cert_bytes)
        tmp.close()
        self.cert_path = tmp.name

        self.sdk = FubonSDK()
        self.sdk.login(fubon_id.strip().upper(), fubon_password, self.cert_path, cert_password)
        self.sdk.init_realtime()

        self.ws = self.sdk.marketdata.websocket_client.stock
        self.ws.on("message", self._on_message)
        self.ws.connect()

        with self.lock:
            self.logged_in = True
            self.connected = True
            self.error = None

    def _parse_message(self, message):
        if isinstance(message, str):
            try:
                return json.loads(message)
            except Exception:
                return {"raw_text": message}
        if isinstance(message, dict):
            return message
        return {"raw_unknown": str(message)}

    def _extract_symbol_price(self, msg):
        data = msg.get("data", {})
        if not isinstance(data, dict):
            data = {}

        symbol = (
            data.get("symbol")
            or msg.get("symbol")
            or data.get("stockNo")
            or msg.get("stockNo")
        )
        if symbol:
            symbol = symbol_to_code(symbol)

        price_candidates = [
            data.get("price"),
            data.get("tradePrice"),
            data.get("lastPrice"),
            data.get("close"),
            data.get("closePrice"),
            msg.get("price"),
            msg.get("tradePrice"),
            msg.get("lastPrice"),
            msg.get("close"),
            msg.get("closePrice"),
        ]

        price = None
        for p in price_candidates:
            if p is not None and pd.notna(p):
                try:
                    price = float(p)
                    break
                except Exception:
                    continue
        return symbol, price

    def _on_message(self, message):
        msg = self._parse_message(message)
        symbol, price = self._extract_symbol_price(msg)
        now = datetime.now(TW_TZ)

        with self.lock:
            self.last_message_at = now
            if symbol:
                self.messages[symbol] = {"time": now, "raw": msg}
            if symbol and price is not None:
                self.prices[symbol] = price

    def subscribe(self, symbol: str):
        if not self.ws:
            return
        code = symbol_to_code(symbol)
        if not code or code in self.subscribed:
            return
        try:
            self.ws.subscribe({"channel": "trades", "symbol": code})
            with self.lock:
                self.subscribed.add(code)
                self.error = None
        except Exception as e:
            with self.lock:
                self.error = f"{code} WebSocket 訂閱失敗：{e}"

    def subscribe_many(self, symbols):
        for s in symbols:
            self.subscribe(s)

    def get_price(self, symbol: str):
        code = symbol_to_code(symbol)
        with self.lock:
            return self.prices.get(code)

    def get_message(self, symbol: str):
        code = symbol_to_code(symbol)
        with self.lock:
            return self.messages.get(code)

    def get_status(self):
        with self.lock:
            return {
                "logged_in": self.logged_in,
                "connected": self.connected,
                "error": self.error,
                "subscribed_count": len(self.subscribed),
                "last_message_at": self.last_message_at,
            }


# =============================================================================
# Fubon REST / 資料函式
# =============================================================================
@st.cache_data(ttl=HISTORY_CACHE_TTL)
def download_stock_data(symbol: str, _sdk=None):
    """
    用 yfinance 取得「今日以前」的歷史日 K。

    設計原則：
    - 當日即時價格：仍由富邦 WebSocket / snapshot 負責。
    - 今日以前歷史資料：全部改用 yfinance。
    - 若 yfinance 回傳今日尚未收盤的日 K，會移除今日資料，避免昨收與 MA/KD 被今日盤中價污染。
    """
    try:
        raw = yf.download(
            symbol,
            period="3mo",
            interval="1d",
            auto_adjust=True,
            progress=False,
            threads=False,
        )
    except Exception as e:
        print(f"yfinance 抓取 {symbol} 歷史 K 線失敗: {e}")
        return pd.DataFrame()

    if raw is None or raw.empty:
        return pd.DataFrame()

    df = raw.copy()

    # yfinance 有時會回傳 MultiIndex columns，這裡轉成單層欄位。
    if isinstance(df.columns, pd.MultiIndex):
        # 常見格式：('Close', '2330.TW')，取第一層 OHLCV 名稱。
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        print(f"yfinance {symbol} 缺少欄位: {missing}")
        return pd.DataFrame()

    df = df.reset_index()

    # Date 欄位名稱可能是 Date 或 Datetime。
    date_col = "Date" if "Date" in df.columns else "Datetime" if "Datetime" in df.columns else df.columns[0]
    df = df.rename(columns={date_col: "Date"})

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])

    # 移除今日資料：目前價格由 WebSocket 即時價處理；歷史資料只保留今日以前。
    today = datetime.now(TW_TZ).date()
    df = df[df["Date"].dt.date < today]

    if df.empty:
        return pd.DataFrame()

    df = df.sort_values("Date").reset_index(drop=True)

    for col in required_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["Open", "High", "Low", "Close"])

    return df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()


def normalize_ohlc(df):
    """保留 Date 欄位，讓 compute_indicators 可以判斷日 K 最後一筆是否為今日。"""
    if df is None or df.empty:
        return pd.DataFrame()

    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    if set(required_cols).issubset(df.columns):
        keep_cols = required_cols.copy()
        if "Date" in df.columns:
            keep_cols = ["Date"] + keep_cols
        return df[keep_cols].copy()

    return pd.DataFrame()


def get_snapshot_price(symbol, _sdk):
    """REST snapshot 只是備援。你之前出現 'market' 錯誤時，這裡會安靜失敗。"""
    if _sdk is None:
        return None
    code = symbol_to_code(symbol)
    try:
        res = _sdk.marketdata.rest_client.stock.snapshot.quotes(symbol=code)
        if res and "data" in res and len(res["data"]) > 0:
            quote = res["data"][0]
            for p in [
                quote.get("tradePrice"),
                quote.get("lastPrice"),
                quote.get("price"),
                quote.get("close"),
                quote.get("closePrice"),
            ]:
                if p is not None and pd.notna(p):
                    try:
                        return float(p)
                    except Exception:
                        continue
    except Exception:
        return None
    return None


def get_last_price_with_source(symbol, df, manager: FubonRealtimeManager):
    """價格優先順序：WebSocket trades -> REST snapshot -> 日 K fallback。"""
    ws_price = manager.get_price(symbol) if manager else None
    if ws_price is not None and pd.notna(ws_price):
        return float(ws_price), "WebSocket trades"

    sdk = manager.sdk if manager else None
    snap_price = get_snapshot_price(symbol, sdk)
    if snap_price is not None and pd.notna(snap_price):
        return float(snap_price), "REST snapshot"

    if df is not None and not df.empty and "Close" in df.columns:
        return float(df["Close"].iloc[-1]), "Daily candle fallback"

    raise ValueError("無法取得即時價格")


@st.cache_data(ttl=86400)
def load_stock_name_map(file_path: str = STOCK_NAME_FILE) -> dict:
    name_map = {}
    if not os.path.exists(file_path):
        return name_map
    with open(file_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip().replace("\ufeff", "").replace("\u3000", "")
            if not line:
                continue
            if "\t" in line:
                parts = [p.strip() for p in line.split("\t") if p.strip()]
                if len(parts) >= 2:
                    name_map[parts[0].upper()] = parts[1].strip()
                    name_map[symbol_to_code(parts[0].upper())] = parts[1].strip()
                    continue
            m = re.match(r"^([^\s]+)\s+(.+)$", line)
            if m:
                symbol = m.group(1).strip().upper()
                name = m.group(2).strip()
                name_map[symbol] = name
                name_map[symbol_to_code(symbol)] = name
    return name_map


@st.cache_data(ttl=86400)
def get_stock_name(symbol: str, _sdk=None) -> str:
    """
    股票名稱來源：
    1. 優先讀 TWstocklistname.txt
    2. 再用 yfinance 的 shortName / longName
    3. 最後回傳股票代碼
    """
    code = symbol_to_code(symbol)
    name_map = load_stock_name_map(STOCK_NAME_FILE)

    if symbol in name_map:
        return name_map[symbol]
    if code in name_map:
        return name_map[code]

    try:
        info = yf.Ticker(symbol).get_info()
        for key in ["shortName", "longName", "displayName", "name"]:
            val = info.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    except Exception:
        pass

    return code


# =============================================================================
# 指標與 UI 格式
# =============================================================================
def compute_indicators(df, price):
    """
    計算即時價格對應的漲跌幅、MA、KD、跳空訊號。

    重要修正：
    - WebSocket price 是盤中即時價。
    - historical.candles 的最後一筆通常是「昨收」，不是今日盤中價。
    - 如果 historical.candles 最後一筆 Date 是今天，才使用倒數第二筆當昨收。
    - 如果 historical.candles 最後一筆 Date 不是今天，使用最後一筆 Close 當昨收。
    """
    if df is None or df.empty:
        raise ValueError("下載資料為空")
    if len(df) < 20:
        raise ValueError("歷史資料不足（至少需要 20 筆）")

    calc_df = df.copy().reset_index(drop=True)

    close = pd.to_numeric(calc_df["Close"].squeeze(), errors="coerce")
    low = pd.to_numeric(calc_df["Low"].squeeze(), errors="coerce")
    high = pd.to_numeric(calc_df["High"].squeeze(), errors="coerce")
    if close.isna().all() or low.isna().all() or high.isna().all():
        raise ValueError("OHLC 資料格式異常")

    price_val = float(price)

    # 判斷日 K 最後一筆是不是今天。
    # 若最後一筆是今天，代表日 K 已包含今日資料，昨收應為倒數第二筆。
    # 若最後一筆不是今天，代表最後一筆本身就是最近完整交易日收盤價，也就是昨收。
    last_row_is_today = False
    if "Date" in calc_df.columns:
        try:
            last_date = pd.to_datetime(calc_df["Date"].iloc[-1]).date()
            today = datetime.now(TW_TZ).date()
            last_row_is_today = last_date == today
        except Exception:
            last_row_is_today = False

    if last_row_is_today:
        if len(close) < 2:
            raise ValueError("昨收資料不足")
        yesterday_close = float(close.iloc[-2])
        yesterday_high = float(high.iloc[-2])
    else:
        yesterday_close = float(close.iloc[-1])
        yesterday_high = float(high.iloc[-1])

    if pd.isna(yesterday_close) or yesterday_close == 0:
        raise ValueError("昨收資料異常")

    change_pct = float((price_val / yesterday_close - 1) * 100)

    # MA / KD 使用歷史日 K。若你之後想讓 MA/KD 也盤中即時，可再把 price_val append 成今日 Close。
    ma5 = float(close.tail(5).mean())
    ma10 = float(close.tail(10).mean())
    ma20 = float(close.tail(20).mean())

    if price_val > ma5:
        ma_range = ">MA5"
    elif ma5 >= price_val > ma10:
        ma_range = "MA5~10"
    elif ma10 >= price_val > ma20:
        ma_range = "MA10~20"
    else:
        ma_range = "<MA20"

    if ma5 > ma10 > ma20:
        ma_trend = "多頭"
    elif ma5 < ma10 < ma20:
        ma_trend = "空頭"
    else:
        ma_trend = "糾結"

    low_9 = low.rolling(9).min()
    high_9 = high.rolling(9).max()
    denominator = (high_9 - low_9).replace(0, pd.NA)
    rsv = ((close - low_9) / denominator) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()

    if len(k.dropna()) < 2 or len(d.dropna()) < 2:
        raise ValueError("KD 計算資料不足")

    k_t = float(k.iloc[-1])
    d_t = float(d.iloc[-1])
    k_y = float(k.iloc[-2])
    d_y = float(d.iloc[-2])

    if k_y <= d_y and k_t > d_t:
        kd_signal = "黃金交叉"
    elif k_y >= d_y and k_t < d_t:
        kd_signal = "死亡交叉"
    elif k_t < d_t and (d_t - k_t) < 3:
        kd_signal = "即將黃金交叉"
    elif k_t > d_t and (k_t - d_t) < 3:
        kd_signal = "即將死亡交叉"
    elif k_t < 25:
        kd_signal = "超賣"
    else:
        kd_signal = "-"

    # 跳空判斷：
    # 若 Date 最後一筆是今天，可用日 K 今日 low；否則盤中最低價尚未接入，先使用即時價作保守替代。
    gap_signal = "-"
    if last_row_is_today:
        today_low = float(low.iloc[-1])
    else:
        today_low = price_val

    if ENABLE_GAP_SIGNAL and pd.notna(today_low) and pd.notna(yesterday_high) and today_low > yesterday_high:
        gap_signal = "跳空"

    return {
        "price": round(price_val, 2),
        "pct": round(change_pct, 2),
        "yesterday_close": round(yesterday_close, 2),
        "ma_range": ma_range,
        "ma_trend": ma_trend,
        "k": round(k_t, 1),
        "d": round(d_t, 1),
        "kd_signal": kd_signal,
        "gap_signal": gap_signal,
    }

def format_color(val):
    if isinstance(val, (int, float)):
        if val > 0:
            return f"🔴 +{val:.2f}%"
        if val < 0:
            return f"🟢 {val:.2f}%"
        return f"{val:.2f}%"
    return val


def format_k(val):
    if isinstance(val, (int, float)):
        if val >= 74:
            return f"🔴 {val:.1f}"
        if val >= 50:
            return f"🟡 {val:.1f}"
        return f"🟢 {val:.1f}"
    return val


def format_gap(val):
    return "🔴 跳空" if val == "跳空" else "-"


def build_top3_html(valid_stock_stats):
    if not valid_stock_stats:
        return '<span style="color:#666666;">無可用資料</span>'
    top3_sorted = sorted(valid_stock_stats, key=lambda x: x["pct"], reverse=True)[:3]
    parts = []
    for item in top3_sorted:
        pct = float(item["pct"])
        pct_color = "#cf1322" if pct > 0 else "#389e0d" if pct < 0 else "#333333"
        code_text = escape(str(item["code"]))
        name_text = escape(str(item["name"]))
        pct_text = f"{pct:+.1f}%"
        parts.append(
            f'<span style="color:#000000;">{code_text} {name_text} </span>'
            f'<span style="color:{pct_color}; font-weight:600;">{pct_text}</span>'
        )
    return " | ".join(parts)


def render_summary_dashboard(group_up_summary, rise_threshold):
    st.markdown("### 📌 漲幅儀表板")
    st.caption(f"目前儀表板統計門檻：漲幅 ≥ {rise_threshold}%")
    html_parts = ['<div class="dashboard-scroll"><div class="dashboard-grid">']

    for item in group_up_summary:
        group_name = escape(str(item["分類"]))
        anchor_id = make_anchor_id(group_name)
        hit_count = item["達標數"]
        total_count = item["總數"]
        up_count = item["上漲數"]
        down_count = item["下跌數"]
        hit_names_text = escape(str(item["達標股票名稱"]))
        top3_html = item["前三名HTML"]

        hit_ratio = (hit_count / total_count * 100) if total_count > 0 else 0
        if hit_ratio >= 60:
            bg_color, border_color, accent_color = "#fff1f0", "#ff7875", "#cf1322"
        elif hit_ratio > 0:
            bg_color, border_color, accent_color = "#fff7e6", "#ffa940", "#d46b08"
        else:
            bg_color, border_color, accent_color = "#f6ffed", "#95de64", "#389e0d"

        html_parts.append(
            f'<a href="#{anchor_id}" class="dashboard-link">'
            f'<div class="dashboard-card" style="background-color:{bg_color}; border:1px solid {border_color}; cursor:pointer;">'
            f'<div class="dashboard-title">{group_name}</div>'
            f'<div class="dashboard-main" style="color:{accent_color};">{hit_count} / {total_count}</div>'
            f'<div class="dashboard-sub">漲幅達標比例（≥{rise_threshold}%）：{hit_ratio:.0f}%</div>'
            f'<div class="dashboard-detail">'
            f'🎯 達標：<b>{hit_count}</b> 檔（{hit_names_text}）<br>'
            f'🔴 一般上漲：<b>{up_count}</b><br>'
            f'🟢 下跌：<b>{down_count}</b>'
            f'</div>'
            f'<div class="dashboard-extra">▶ {top3_html}</div>'
            f'</div></a>'
        )
    html_parts.append("</div></div>")
    st.markdown("".join(html_parts), unsafe_allow_html=True)

# =============================================================================
# Session State 初始化
# =============================================================================
if "auto_refresh_enabled" not in st.session_state:
    st.session_state.auto_refresh_enabled = True
if "tg_push_enabled" not in st.session_state:
    st.session_state.tg_push_enabled = False
if "scheduled_push_enabled" not in st.session_state:
    st.session_state.scheduled_push_enabled = True
if "processed_time_slots" not in st.session_state:
    st.session_state.processed_time_slots = set()
if "stock_groups" not in st.session_state:
    st.session_state.stock_groups = load_stock_groups()
if "group_editor_unlocked" not in st.session_state:
    st.session_state.group_editor_unlocked = False
if "editing_mode" not in st.session_state:
    st.session_state.editing_mode = False
if "fubon_manager" not in st.session_state:
    st.session_state.fubon_manager = FubonRealtimeManager()
if "fubon_logged_in" not in st.session_state:
    st.session_state.fubon_logged_in = False
if "selected_group_editor" not in st.session_state:
    group_names_init = list(st.session_state.stock_groups.keys())
    st.session_state.selected_group_editor = group_names_init[0] if group_names_init else ""
if "rename_group_input" not in st.session_state:
    st.session_state.rename_group_input = st.session_state.selected_group_editor
if "symbols_text_area" not in st.session_state:
    selected = st.session_state.selected_group_editor
    st.session_state.symbols_text_area = "\n".join(st.session_state.stock_groups.get(selected, []))
if "quick_add_symbol_input" not in st.session_state:
    st.session_state.quick_add_symbol_input = ""
if "notified_stocks" not in st.session_state:
    st.session_state.notified_stocks = set()
if "tg_last_update_id" not in st.session_state:
    st.session_state.tg_last_update_id = None
if "_next_selected_group" in st.session_state:
    pending_group = st.session_state._next_selected_group
    del st.session_state._next_selected_group
    if pending_group in st.session_state.stock_groups:
        st.session_state.selected_group_editor = pending_group
        st.session_state.rename_group_input = pending_group
        st.session_state.symbols_text_area = "\n".join(st.session_state.stock_groups.get(pending_group, []))


def set_next_selected_group(group_name: str):
    st.session_state._next_selected_group = group_name


def enter_edit_mode():
    st.session_state.editing_mode = True


def leave_edit_mode():
    st.session_state.editing_mode = False


def sync_editor_fields_from_selected_group():
    groups = st.session_state.stock_groups
    selected_group = st.session_state.selected_group_editor
    if selected_group not in groups:
        group_names = list(groups.keys())
        selected_group = group_names[0] if group_names else ""
        st.session_state.selected_group_editor = selected_group
    st.session_state.rename_group_input = selected_group
    st.session_state.symbols_text_area = "\n".join(groups.get(selected_group, []))
    st.session_state.editing_mode = False

# =============================================================================
# UI 元件
# =============================================================================
def render_fubon_login():
    st.sidebar.markdown("## 🔑 富邦 API 設定 (Fubon Neo)")

    manager: FubonRealtimeManager = st.session_state.fubon_manager
    status = manager.get_status()

    if st.session_state.fubon_logged_in:
        st.sidebar.success("✅ 富邦 API 已成功連線")
        ws_text = "Connected" if status["connected"] else "Disconnected"
        st.sidebar.caption(f"WebSocket: {ws_text}")
        st.sidebar.caption(f"已訂閱：{status['subscribed_count']} 檔")
        if status["last_message_at"]:
            st.sidebar.caption(f"最後 WS：{status['last_message_at'].strftime('%H:%M:%S')}")
        if status["error"]:
            st.sidebar.warning(status["error"])

        if st.sidebar.button("登出 / 重新連線", use_container_width=True):
            st.session_state.fubon_manager = FubonRealtimeManager()
            st.session_state.fubon_logged_in = False
            st.cache_data.clear()
            st.rerun()
        return

    try:
        fubon_secrets = st.secrets["fubon"]
        pfx_base64 = fubon_secrets["pfx_base64"]
    except Exception:
        st.sidebar.error("❌ 找不到 Streamlit Secrets 中的 [fubon].pfx_base64 憑證資料。")
        return

    st.sidebar.info("請輸入富邦證券登入資訊")
    f_id = st.sidebar.text_input("身分證字號", key="f_id_input")
    f_pw = st.sidebar.text_input("富邦登入密碼", key="f_pw_input", type="password")
    f_cert_pw = st.sidebar.text_input("憑證密碼", key="f_cert_pw_input", type="password")

    if st.sidebar.button("連線行情伺服器", use_container_width=True):
        if not f_id or not f_pw or not f_cert_pw:
            st.sidebar.warning("請填寫完整的身分證字號與密碼！")
        else:
            try:
                with st.spinner("連線富邦 API / WebSocket 中..."):
                    manager.login(f_id, f_pw, f_cert_pw, pfx_base64)
                    st.session_state.fubon_logged_in = True
                st.sidebar.success("✅ 富邦 API / WebSocket 連線成功！")
                st.rerun()
            except Exception as e:
                st.sidebar.error(f"❌ 登入失敗: {e}")


def render_group_editor_lock():
    st.sidebar.markdown("## 🔐 分組編輯鎖")
    if st.session_state.group_editor_unlocked:
        st.sidebar.success("已解鎖，可編輯股票分組")
        st.sidebar.info("為避免編輯中被重刷，分組編輯解鎖時會暫停自動更新")
        if st.sidebar.button("鎖定編輯", key="lock_group_editor_btn", use_container_width=True):
            st.session_state.group_editor_unlocked = False
            leave_edit_mode()
            st.rerun()
        return

    pin_input = st.sidebar.text_input("請輸入 PIN 碼以編輯分組", type="password", key="group_edit_pin_input")
    if st.sidebar.button("解鎖編輯", key="unlock_group_editor_btn", use_container_width=True):
        if pin_input == GROUP_EDIT_PIN:
            st.session_state.group_editor_unlocked = True
            enter_edit_mode()
            st.sidebar.success("PIN 正確，已解鎖")
            st.rerun()
        else:
            st.sidebar.error("PIN 錯誤")


def render_stock_group_editor():
    st.sidebar.markdown("## 🛠️ 股票分組編輯")
    groups = st.session_state.stock_groups
    group_names = list(groups.keys())

    if not group_names:
        st.session_state.stock_groups = copy.deepcopy(DEFAULT_STOCK_GROUPS)
        groups = st.session_state.stock_groups
        group_names = list(groups.keys())

    if st.session_state.selected_group_editor not in group_names:
        first_group = group_names[0]
        st.session_state.selected_group_editor = first_group
        st.session_state.rename_group_input = first_group
        st.session_state.symbols_text_area = "\n".join(groups.get(first_group, []))

    with st.sidebar.expander("➕ 新增分類", expanded=False):
        new_group_name = st.text_input("分類名稱", key="new_group_name_input")
        if st.button("新增分類", key="add_group_btn", use_container_width=True):
            enter_edit_mode()
            name = new_group_name.strip()
            if not name:
                st.sidebar.warning("請輸入分類名稱")
            elif name in groups:
                st.sidebar.warning("分類名稱已存在")
            else:
                groups[name] = []
                st.session_state.stock_groups = groups
                save_stock_groups(groups)
                set_next_selected_group(name)
                st.rerun()

    with st.sidebar.expander("📝 編輯分類", expanded=True):
        st.selectbox("選擇分類", options=group_names, key="selected_group_editor", on_change=sync_editor_fields_from_selected_group)
        selected_group = st.session_state.selected_group_editor
        new_group_name = st.text_input("分類名稱（可修改）", key="rename_group_input", on_change=enter_edit_mode)
        symbols_text = st.text_area("股票清單（每行一檔，或逗號分隔）", height=220, key="symbols_text_area", on_change=enter_edit_mode)

        st.markdown("### ⚡ 快速新增股票搜尋")
        quick_col1, quick_col2 = st.columns([2, 1])
        with quick_col1:
            quick_input = st.text_input("輸入股票代碼或 ticker", key="quick_add_symbol_input", on_change=enter_edit_mode)
        normalized_quick_symbol = normalize_symbol_quick(quick_input)
        if normalized_quick_symbol:
            st.caption(f"標準化代碼：{normalized_quick_symbol}")
        with quick_col2:
            if st.button("加入目前分類", key="quick_add_btn", use_container_width=True):
                enter_edit_mode()
                symbol = normalize_symbol_quick(quick_input)
                if not symbol:
                    st.warning("請輸入股票代碼")
                else:
                    current_list = groups.get(selected_group, [])
                    if symbol in current_list:
                        st.warning("此股票已存在於目前分類")
                    else:
                        current_list.append(symbol)
                        groups[selected_group] = current_list
                        st.session_state.stock_groups = groups
                        save_stock_groups(groups)
                        st.session_state.symbols_text_area = "\n".join(current_list)
                        st.session_state.quick_add_symbol_input = ""
                        st.success(f"已加入 {symbol}")
                        st.rerun()

        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 儲存分類", key="save_group_btn", use_container_width=True):
                new_name = new_group_name.strip()
                if not new_name:
                    st.sidebar.warning("分類名稱不可為空")
                elif new_name != selected_group and new_name in groups:
                    st.sidebar.warning("分類名稱已存在，請使用其他名稱")
                else:
                    new_symbols = normalize_symbols_from_text(symbols_text)
                    updated = {}
                    for k, v in groups.items():
                        updated[new_name if k == selected_group else k] = new_symbols if k == selected_group else v
                    st.session_state.stock_groups = updated
                    save_stock_groups(updated)
                    leave_edit_mode()
                    set_next_selected_group(new_name)
                    st.rerun()
        with col2:
            if st.button("🗑️ 刪除分類", key="delete_group_btn", use_container_width=True):
                if len(groups) <= 1:
                    st.sidebar.warning("至少保留一個分類")
                else:
                    groups.pop(selected_group, None)
                    st.session_state.stock_groups = groups
                    save_stock_groups(groups)
                    leave_edit_mode()
                    set_next_selected_group(list(groups.keys())[0])
                    st.rerun()

    with st.sidebar.expander("📦 備份 / 匯出 / 匯入 JSON", expanded=False):
        export_json_str = json.dumps(st.session_state.stock_groups, ensure_ascii=False, indent=2)
        st.download_button("⬇️ 匯出目前分組 JSON", export_json_str, "stock_groups.json", "application/json", key="download_groups_json_btn", use_container_width=True)
        if st.button("🗂️ 建立本地備份", key="create_local_backup_btn", use_container_width=True):
            try:
                backup_file = save_backup_snapshot(st.session_state.stock_groups)
                st.sidebar.success(f"已建立備份：{os.path.basename(backup_file)}")
            except Exception as e:
                st.sidebar.error(f"建立備份失敗：{e}")
        uploaded_file = st.file_uploader("上傳股票分組 JSON", type=["json"], key="upload_groups_json_file")
        if uploaded_file is not None:
            if st.button("📥 匯入並覆蓋目前分組", key="import_groups_json_btn", use_container_width=True):
                try:
                    data = json.loads(uploaded_file.read().decode("utf-8"))
                    validated = validate_and_normalize_group_json(data)
                    save_backup_snapshot(st.session_state.stock_groups)
                    st.session_state.stock_groups = validated
                    save_stock_groups(validated)
                    leave_edit_mode()
                    set_next_selected_group(list(validated.keys())[0])
                    st.sidebar.success("JSON 匯入成功，已覆蓋目前股票分組")
                    st.rerun()
                except Exception as e:
                    st.sidebar.error(f"JSON 匯入失敗：{e}")
        backups = list_backup_files()
        if backups:
            st.markdown("**最近備份檔**")
            for name in backups[:5]:
                st.caption(name)
        else:
            st.caption("目前沒有本地備份檔")

    with st.sidebar.expander("♻️ 重設", expanded=False):
        if st.button("還原預設分組", key="reset_groups_btn", use_container_width=True):
            try:
                save_backup_snapshot(st.session_state.stock_groups)
            except Exception:
                pass
            st.session_state.stock_groups = copy.deepcopy(DEFAULT_STOCK_GROUPS)
            save_stock_groups(st.session_state.stock_groups)
            leave_edit_mode()
            set_next_selected_group(list(st.session_state.stock_groups.keys())[0])
            st.rerun()

    with st.sidebar.expander("👀 分組預覽", expanded=False):
        for g, symbols in st.session_state.stock_groups.items():
            st.markdown(f"**{g}**（{len(symbols)}檔）")
            st.caption(", ".join(symbols) if symbols else "（空）")

# =============================================================================
# 主畫面
# =============================================================================
st.title("📊 股票監控面板 - 富邦即時價 + yfinance歷史")
st.markdown('<div id="dashboard-top"></div>', unsafe_allow_html=True)

if FubonSDK is None:
    st.error("富邦 SDK 載入失敗：請確認已安裝官方 fubon_neo SDK，且執行環境 Python 版本相容。")
    st.stop()

col1, col2, col3, col4 = st.columns([1, 1, 1, 1])
with col1:
    if st.button("🔄 手動更新即時資料 (清除歷史快取)", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with col2:
    auto_refresh = st.toggle(f"⏱️ 啟用自動更新 ({REFRESH_SEC}秒)", value=st.session_state.auto_refresh_enabled)
    if auto_refresh != st.session_state.auto_refresh_enabled:
        st.session_state.auto_refresh_enabled = auto_refresh
        st.rerun()
with col3:
    tg_push = st.toggle("📲 Telegram 推送開關", value=st.session_state.tg_push_enabled)
    if tg_push != st.session_state.tg_push_enabled:
        st.session_state.tg_push_enabled = tg_push
        st.rerun()
with col4:
    sched_push = st.toggle("⏰ 定時推送模式", value=st.session_state.scheduled_push_enabled)
    if sched_push != st.session_state.scheduled_push_enabled:
        st.session_state.scheduled_push_enabled = sched_push
        st.rerun()

gc.collect()

render_fubon_login()
render_group_editor_lock()
if st.session_state.group_editor_unlocked:
    render_stock_group_editor()
else:
    st.sidebar.info("目前為唯讀模式：輸入 PIN 後才能修改股票分組")

tw_now = datetime.now(TW_TZ)
st.caption(f"更新時間：{tw_now.strftime('%Y-%m-%d %H:%M:%S')}")
rise_threshold = st.slider("儀表板漲幅達標門檻 (%)", min_value=5, max_value=9, value=5, step=1)

if not st.session_state.fubon_logged_in:
    st.warning("⚠️ 請先至左側面板連線「富邦 API」，才能抓取當日即時價；今日以前歷史資料由 yfinance 取得。")
    st.stop()

manager: FubonRealtimeManager = st.session_state.fubon_manager
manager_status = manager.get_status()

# 登入後訂閱所有股票
all_symbols = []
for stocks in st.session_state.stock_groups.values():
    all_symbols.extend(stocks)
manager.subscribe_many(all_symbols)

with st.sidebar.expander("📡 WebSocket 狀態", expanded=True):
    status = manager.get_status()
    if status["connected"]:
        st.markdown('<span class="ws-ok">● Connected</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="ws-bad">● Disconnected</span>', unsafe_allow_html=True)
    st.caption(f"已訂閱：{status['subscribed_count']} 檔")
    if status["last_message_at"]:
        st.caption(f"最後資料：{status['last_message_at'].strftime('%H:%M:%S')}")
    if status["error"]:
        st.warning(status["error"])

# ===== 推送時間與手動指令邏輯判斷 =====
can_push_now = False
current_schedule_key = None
manual_push_triggered = False
if st.session_state.tg_push_enabled:
    manual_push_triggered = check_telegram_push_command()
    if manual_push_triggered:
        can_push_now = True
        st.session_state.notified_stocks = set()
        st.toast("🚀 收到 'push' 指令，強制觸發推播！")
        send_telegram_message("🤖 <b>收到指令，開始為您掃描並強制推播強勢股...</b>")
    elif st.session_state.scheduled_push_enabled:
        TARGET_TIMES = [
            tw_now.replace(hour=9, minute=40, second=0, microsecond=0),
            tw_now.replace(hour=10, minute=0, second=0, microsecond=0),
            tw_now.replace(hour=11, minute=0, second=0, microsecond=0),
            tw_now.replace(hour=12, minute=0, second=0, microsecond=0),
            tw_now.replace(hour=13, minute=0, second=0, microsecond=0),
        ]
        for target_dt in TARGET_TIMES:
            if abs((tw_now - target_dt).total_seconds()) <= 45:
                current_schedule_key = f"slot_{tw_now.strftime('%Y%m%d')}_{target_dt.strftime('%H%M')}"
                if current_schedule_key not in st.session_state.processed_time_slots:
                    can_push_now = True
                    break

# ===== 掃描與顯示 =====
group_tables = {}
group_up_summary = []

for group_name, stocks in st.session_state.stock_groups.items():
    rows = []
    hit_count = up_count = down_count = flat_count = error_count = 0
    valid_stock_stats = []
    hit_names = []

    for symbol in stocks:
        try:
            df = download_stock_data(symbol, manager.sdk)
            df = normalize_ohlc(df)
            if df.empty:
                raise ValueError("無效的 K 線資料")

            price, price_source = get_last_price_with_source(symbol, df, manager)
            stock_name = get_stock_name(symbol, manager.sdk)
            data = compute_indicators(df, price)

            is_high_gain = data["pct"] >= 5
            has_kd_signal = data["kd_signal"] in ["黃金交叉", "即將黃金交叉"]
            has_gap_signal = data["gap_signal"] == "跳空"

            if is_high_gain or has_kd_signal or has_gap_signal:
                base_symbol = symbol_to_code(symbol)
                yahoo_url = f"https://tw.stock.yahoo.com/quote/{base_symbol}"
                symbol_link = f'<a href="{yahoo_url}">{symbol}</a>'
                today_str = tw_now.strftime("%Y-%m-%d")
                notify_key = f"{symbol}_{today_str}"
                if can_push_now and notify_key not in st.session_state.notified_stocks:
                    msg = (
                        f"🔔 <b>強勢股達標通知：{stock_name} ({symbol_link})</b>\n\n"
                        f"📈 價格：{data['price']}\n"
                        f"🔥 漲幅：+{data['pct']}%\n"
                        f"📊 KD訊號：{data['kd_signal']}\n"
                        f"🚀 跳空訊號：{data['gap_signal']}\n"
                        f"📡 價格來源：{price_source}"
                    )
                    send_telegram_message(msg)
                    st.session_state.notified_stocks.add(notify_key)

            if data["pct"] >= rise_threshold:
                hit_count += 1
                hit_names.append(stock_name)
            if data["pct"] > 0:
                up_count += 1
            elif data["pct"] < 0:
                down_count += 1
            else:
                flat_count += 1

            valid_stock_stats.append({"symbol": symbol, "code": symbol_to_code(symbol), "name": stock_name, "pct": float(data["pct"])})
            rows.append({
                "代碼": symbol,
                "代碼網址": yahoo_quote_url(symbol),
                "股票名稱": stock_name,
                "價格": f"{data['price']:.2f}",
                "昨收": f"{data['yesterday_close']:.2f}",
                "漲跌%": data["pct"],
                "MA位置": data["ma_range"],
                "MA排列": data["ma_trend"],
                "K值": data["k"],
                "D值": f"{data['d']:.1f}",
                "KD訊號": data["kd_signal"],
                "跳空訊號": data["gap_signal"],
                "價格來源": price_source,
            })
        except Exception as e:
            error_count += 1
            rows.append({
                "代碼": symbol,
                "代碼網址": "",
                "股票名稱": get_stock_name(symbol, manager.sdk),
                "價格": "錯誤",
                "昨收": "-",
                "漲跌%": "-",
                "MA位置": "-",
                "MA排列": "-",
                "K值": "-",
                "D值": "-",
                "KD訊號": "-",
                "跳空訊號": str(e),
                "價格來源": "-",
            })

    hit_names_text = compact_name_list(hit_names, max_show=4)
    top3_html = build_top3_html(valid_stock_stats)
    df_table = pd.DataFrame(rows)
    display_df = df_table.copy()
    if not display_df.empty:
        display_df["漲跌%"] = display_df["漲跌%"].apply(format_color)
        display_df["K值"] = display_df["K值"].apply(format_k)
        display_df["跳空訊號"] = display_df["跳空訊號"].apply(format_gap)
    group_tables[group_name] = {"count": len(stocks), "table": display_df}
    group_up_summary.append({
        "分類": group_name,
        "達標數": hit_count,
        "達標股票名稱": hit_names_text,
        "前三名HTML": top3_html,
        "上漲數": up_count,
        "下跌數": down_count,
        "平盤數": flat_count,
        "錯誤數": error_count,
        "總數": len(stocks),
    })

if can_push_now and st.session_state.scheduled_push_enabled and current_schedule_key and not manual_push_triggered:
    st.session_state.processed_time_slots.add(current_schedule_key)

render_summary_dashboard(group_up_summary, rise_threshold)
st.divider()

for group_name, info in group_tables.items():
    anchor_id = make_anchor_id(group_name)
    st.markdown(f'<div id="{anchor_id}" style="scroll-margin-top: 80px;"></div>', unsafe_allow_html=True)
    header_col1, header_col2 = st.columns([8, 2])
    with header_col1:
        st.subheader(f"【{group_name}】({info['count']}檔)")
    with header_col2:
        st.markdown('<div style="text-align:right; padding-top:0.4rem;"><a href="#dashboard-top" class="back-to-dashboard-btn">⬆ 回到儀表板</a></div>', unsafe_allow_html=True)

    table_df = info["table"].copy()
    if not table_df.empty and "代碼網址" in table_df.columns:
        table_df["代碼"] = table_df["代碼網址"]

    display_columns = ["代碼", "股票名稱", "價格", "昨收", "漲跌%", "MA位置", "MA排列", "K值", "D值", "KD訊號", "跳空訊號", "價格來源"]
    st.dataframe(
        table_df[display_columns],
        use_container_width=True,
        column_config={
            "代碼": st.column_config.LinkColumn("代碼", help="點擊前往台股 Yahoo", display_text=r"https://tw.stock.yahoo.com/quote/(.*)"),
            "股票名稱": st.column_config.TextColumn("股票名稱"),
        },
    )
    st.markdown('<div style="margin-bottom: 10px;"></div>', unsafe_allow_html=True)

# Debug: 查看某檔最新 WebSocket 原始訊息
with st.sidebar.expander("🔍 WebSocket Debug", expanded=False):
    debug_code = st.text_input("輸入代碼看最後 WS 原始訊息", value="4919")
    msg = manager.get_message(debug_code)
    if msg:
        st.caption(f"時間：{msg['time'].strftime('%Y-%m-%d %H:%M:%S')}")
        st.json(msg["raw"])
    else:
        st.caption("尚未收到此代碼的 WebSocket 訊息")

if st.session_state.auto_refresh_enabled and not st.session_state.group_editor_unlocked and not st.session_state.editing_mode:
    time.sleep(REFRESH_SEC)
    st.rerun()
