# -*- coding: utf-8 -*-
"""
股票監控面板 - 富邦 WebSocket 即時價 + yfinance 歷史資料

本版修正 / 改進：
1. 修正 HTML 被寫成 &lt; / &gt; 導致錨點與按鈕無法跳轉的問題。
2. 修正 APP_LOGO 字串少了結尾引號的語法錯誤。
3. 新增 dashboard-top 錨點，「回到儀表板」可正常跳轉。
4. 儀表板卡片與分類錨點改成真正 HTML。
6. 圖片不存在時不會中斷，改顯示文字標題。
7. 移除 TWO / TW 猜測邏輯，全面改為查表。
"""

import re
import os
import json
import copy
import time
import gc
import base64
import tempfile
import threading
import sqlite3
import requests
from html import escape
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yfinance as yf
import plotly.graph_objects as go

st.set_page_config(page_title="台股監控面板", layout="wide")

# ===== 富邦 API 引入 =====
try:
    from fubon_neo.sdk import FubonSDK
except Exception:
    FubonSDK = None

# ===== 常數設定 =====
TW_TZ = ZoneInfo("Asia/Taipei")
REFRESH_SEC = 3
YFINANCE_HISTORY_CACHE_TTL_SEC = 60 * 60  # yfinance 今日以前歷史資料每小時更新一次
HISTORY_CACHE_TTL = YFINANCE_HISTORY_CACHE_TTL_SEC
LIMIT_UP_DOWN_PCT_THRESHOLD = 9.5  # 台股漲跌停約為 ±10%，抓 9.5% 以上視為漲/跌停（含極端接近漲跌停）
GROUP_EDIT_PIN = "1219"
GROUPS_FILE = "stock_groups.json"
BACKUP_DIR = "backups"
STOCK_NAME_FILE = "TWstocklistname2.txt"
APP_LOGO = "jerry.jpg"
TWSE_DB_PATH = "twse_ohlcv.db"  # 本機 SQLite 歷史 OHLCV 資料庫（表格：ohlcv_data）
DB_HISTORY_CACHE_TTL_SEC = 60 * 60  # twse_ohlcv.db 歷史資料每小時更新一次
DB_LATEST_PRICE_CACHE_TTL_SEC = 30  # twse_ohlcv.db「全部資料」模式下的當日價格快取秒數

# ===== Secrets 安全讀取 =====
def get_secret_or_default(key: str, default: str = ""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


# ===== Telegram 設定 =====
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
st.markdown(
    """
<style>
html { scroll-behavior: smooth; }
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
.ws-ok { color: #16a34a; font-weight: 700; }
.ws-bad { color: #dc2626; font-weight: 700; }
</style>
""",
    unsafe_allow_html=True,
)

# =============================================================================
# 基礎工具函式（已移除猜測邏輯，全面改為查表）
# =============================================================================
def symbol_to_code(symbol: str) -> str:
    return str(symbol).strip().upper().split(".")[0]


def yahoo_quote_url(symbol: str) -> str:
    raw_code = symbol_to_code(symbol)
    code = raw_code.split('/')[0]
    # 回傳「純網址」字串,交給 st.column_config.LinkColumn 處理顯示文字與跳轉
    return f"https://tw.stock.yahoo.com/quote/{code}/technical-analysis"


def make_anchor_id(group_name: str) -> str:
    anchor = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", group_name).strip("-")
    return f"group-{anchor}"


@st.cache_data(ttl=86400)
def load_stock_lookup_maps(file_path: str = STOCK_NAME_FILE) -> dict:
    code_to_name = {}
    code_to_symbol = {}
    name_to_symbol = {}
    if not os.path.exists(file_path):
        return {"code_to_name": code_to_name, "code_to_symbol": code_to_symbol, "name_to_symbol": name_to_symbol}
    with open(file_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            line = line.replace("\ufeff", "").replace("\u3000", " ").strip()
            if "\t" in line:
                parts = [p.strip() for p in line.split("\t") if p.strip()]
            else:
                m = re.match(r"^([^\s]+)\s+(.+)$", line)
                parts = [m.group(1).strip(), m.group(2).strip()] if m else []
            if len(parts) < 2:
                continue
            raw_symbol = parts[0].upper()
            stock_name = parts[1].strip()
            
            # normalize_lookup_symbol 內聯邏輯避免循環
            symbol = raw_symbol
            code = symbol_to_code(symbol)
            if not code or not stock_name:
                continue
            code_to_name[code] = stock_name
            code_to_symbol[code] = symbol
            name_to_symbol[stock_name] = symbol
            name_to_symbol[stock_name.replace(" ", "")] = symbol
    return {"code_to_name": code_to_name, "code_to_symbol": code_to_symbol, "name_to_symbol": name_to_symbol}


def normalize_lookup_symbol(raw_symbol: str) -> str:
    s = str(raw_symbol).strip().upper()
    if not s:
        return ""
    if "." in s:
        return s
    return s


def normalize_symbol_quick(input_text: str):
    s = str(input_text).strip().upper()
    if not s:
        return None
    if "." in s:
        return s
    
    # 移除原本寫死的 3, 6, 8 開頭猜測，改為直接查表對應的完整代碼 (含 .TW / .TWO)[cite: 1, 2]
    if s.isdigit():
        try:
            lookup = load_stock_lookup_maps(STOCK_NAME_FILE)
            code_to_symbol = lookup.get("code_to_symbol", {})
            if s in code_to_symbol:
                return code_to_symbol[s]
        except Exception:
            pass
            
    return s


def build_yfinance_candidates(symbol: str):
    raw = str(symbol).strip().upper()
    code = symbol_to_code(raw)
    candidates = []
    
    if raw and "." in raw:
        candidates.append(raw)
    else:
        normalized = normalize_symbol_quick(raw)
        if normalized:
            candidates.append(normalized)
            
    # 移除直接硬加 .TW 與 .TWO 的舊機制，改由查表提供準確後綴[cite: 1, 2]
    result, seen = [], set()
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def normalize_symbols_from_text(text: str):
    if not text:
        return []
    text = text.replace("，", ",")
    lines = []
    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        parts = [p.strip().upper() for p in raw_line.split(",") if p.strip()]
        lines.extend(parts)
    seen = set()
    result = []
    for s in lines:
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
# 富邦 WebSocket：只負責「當日即時股價」
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

        try:
            if self.ws is not None:
                self.ws.disconnect()
        except Exception:
            pass

        with self.lock:
            self.sdk = None
            self.ws = None
            self.logged_in = False
            self.connected = False
            self.error = None
            self.prices = {}
            self.messages = {}
            self.subscribed = set()
            self.last_message_at = None

        pfx_base64 = str(pfx_base64).strip()
        if "," in pfx_base64 and "base64" in pfx_base64[:80].lower():
            pfx_base64 = pfx_base64.split(",", 1)[1].strip()

        try:
            cert_bytes = base64.b64decode(pfx_base64, validate=True)
        except Exception as e:
            raise RuntimeError(f"pfx_base64 不是有效的 Base64 憑證資料：{e}")
        if not cert_bytes:
            raise RuntimeError("pfx_base64 解碼後是空資料")

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pfx")
        tmp.write(cert_bytes)
        tmp.close()
        self.cert_path = tmp.name

        sdk = None
        ws = None
        try:
            sdk = FubonSDK()
            login_result = sdk.login(
                fubon_id.strip().upper(),
                fubon_password,
                self.cert_path,
                cert_password,
            )
            is_success = getattr(login_result, "is_success", None)
            message = getattr(login_result, "message", None)
            if is_success is False:
                raise RuntimeError(f"富邦登入失敗：{message or login_result}")

            sdk.init_realtime()
            ws = sdk.marketdata.websocket_client.stock
            ws.on("message", self._on_message)
            ws.connect()

            with self.lock:
                self.sdk = sdk
                self.ws = ws
                self.logged_in = True
                self.connected = True
                self.error = None
        except Exception as e:
            try:
                if ws is not None:
                    ws.disconnect()
            except Exception:
                pass
            with self.lock:
                self.sdk = None
                self.ws = None
                self.logged_in = False
                self.connected = False
                self.error = str(e)
                self.prices = {}
                self.messages = {}
                self.subscribed = set()
                self.last_message_at = None
            raise

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
        symbol = data.get("symbol") or msg.get("symbol") or data.get("stockNo") or msg.get("stockNo")
        if symbol:
            symbol = symbol_to_code(symbol)
        price_candidates = [
            data.get("price"), data.get("tradePrice"), data.get("lastPrice"),
            data.get("close"), data.get("closePrice"),
            msg.get("price"), msg.get("tradePrice"), msg.get("lastPrice"),
            msg.get("close"), msg.get("closePrice"),
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
            return copy.deepcopy(self.messages.get(code))

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
# 富邦 REST：TSE 加權指數（TAIEX / IX0001）即時走勢
# =============================================================================
TAIEX_SYMBOL = "IX0001"


@st.cache_data(ttl=15, show_spinner=False)
def fetch_taiex_intraday(_sdk):
    """
    透過富邦 API 的 reststock.intraday.quote / intraday.candles 取得
    台股加權指數（TSE，代碼 IX0001）當日即時報價與分鐘走勢。
    _sdk 開頭加底線，st.cache_data 不會嘗試對它做 hash。
    """
    reststock = _sdk.marketdata.rest_client.stock
    quote = reststock.intraday.quote(symbol=TAIEX_SYMBOL)
    candles = reststock.intraday.candles(symbol=TAIEX_SYMBOL)
    return {"quote": quote, "candles": candles}


# =============================================================================
# 富邦 REST：個股「今天」官方 OHLC（取代自己土法煉鋼追蹤的 session_low）
# =============================================================================
# 富邦官方 REST API 的 intraday.quote() 會直接回傳「今天」交易所算好的
# openPrice / highPrice / lowPrice / lastPrice，這是 100% 準確的今日最低價，
# 不需要自己在 WebSocket 逐筆成交訊息裡累加追蹤，也不會有 fallback 價格
# （yfinance / 歷史收盤）污染追蹤值的問題。
#
# 用 st.cache_data 做較長的快取（預設 20 秒），是因為主畫面每 REFRESH_SEC
# （預設 3 秒）就會整頁重跑一次；如果每次重跑都對每一檔股票呼叫一次 REST，
# 在多檔股票、多個群組的情況下很容易撞到富邦 REST 的流量限制（429 Rate limit
# exceeded）。今日最低價這種資料一旦成立基本不會頻繁變動，用 20 秒快取
# 對「跳空判斷」這個用途來說已經足夠即時。
FUBON_OHLC_CACHE_TTL_SEC = 20


@st.cache_data(ttl=FUBON_OHLC_CACHE_TTL_SEC, show_spinner=False)
def fetch_fubon_intraday_ohlc(_sdk, code: str):
    """
    透過富邦官方 REST API 取得指定股票「今天」的官方 OHLC。
    _sdk 開頭加底線，st.cache_data 不會嘗試對它做 hash。
    回傳失敗時丟出例外，由呼叫端決定是否要 fallback 回自己追蹤的邏輯。
    """
    reststock = _sdk.marketdata.rest_client.stock
    quote = reststock.intraday.quote(symbol=code)
    return {
        "previousClose": quote.get("previousClose"),
        "openPrice": quote.get("openPrice"),
        "highPrice": quote.get("highPrice"),
        "lowPrice": quote.get("lowPrice"),
        "lastPrice": quote.get("lastPrice"),
    }


def get_official_today_low(manager, symbol: str):
    """
    嘗試用富邦 REST API 取得「今天」官方最低價（lowPrice）。
    任何情況取得失敗（未登入、非交易時段、觸發流量限制等）都回傳 None，
    讓呼叫端可以無痛 fallback 回自己追蹤的 session_low 邏輯，不會讓整個
    畫面因為這支 API 失敗而掛掉。
    """
    try:
        sdk = getattr(manager, "sdk", None)
        if sdk is None:
            return None
        code = symbol_to_code(symbol)
        ohlc = fetch_fubon_intraday_ohlc(sdk, code)
        low_price = ohlc.get("lowPrice")
        if low_price is None:
            return None
        return float(low_price)
    except Exception:
        return None


def get_official_today_ohlc(manager, symbol: str) -> dict:
    """
    嘗試用富邦 REST API 取得「今天」官方開高低價（openPrice/highPrice/lowPrice）。
    供 signal_module 訊號計算使用（很多型態訊號需要真正的當日開高低，
    不是只靠單一 tick 價格）。任何情況取得失敗都回傳全 None 的 dict，
    讓呼叫端可以無痛 fallback 回自己追蹤的 session_low/session_high 邏輯。
    """
    try:
        sdk = getattr(manager, "sdk", None)
        if sdk is None:
            return {"open": None, "high": None, "low": None}
        code = symbol_to_code(symbol)
        ohlc = fetch_fubon_intraday_ohlc(sdk, code)

        def _to_float(v):
            try:
                return float(v) if v is not None else None
            except Exception:
                return None

        return {
            "open": _to_float(ohlc.get("openPrice")),
            "high": _to_float(ohlc.get("highPrice")),
            "low": _to_float(ohlc.get("lowPrice")),
        }
    except Exception:
        return {"open": None, "high": None, "low": None}


def render_taiex_chart():
    st.markdown("#### 📈 台股加權指數（TSE）即時走勢")

    manager = st.session_state.fubon_manager
    sdk = getattr(manager, "sdk", None)

    if not st.session_state.fubon_logged_in or sdk is None:
        st.info("尚未登入富邦帳號，登入後即可顯示加權指數即時走勢（資料來源：富邦 API）。")
        return

    try:
        data = fetch_taiex_intraday(sdk)
    except Exception as e:
        st.warning(f"加權指數資料取得失敗：{e}")
        return

    quote = data.get("quote") or {}
    candles_resp = data.get("candles") or {}
    rows = candles_resp.get("data") or []

    if not rows:
        st.info("目前無加權指數分鐘資料（可能尚未開盤或休市）。")
        return

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])

    # 交易時段固定為 09:00–13:30，X 軸也固定這個範圍、每 30 分鐘一格，
    # 不會因為資料只到某個時間點就把整條線拉長或壓縮版面。
    session_date = df["date"].iloc[0].date()
    session_tz = df["date"].dt.tz
    session_start = pd.Timestamp.combine(session_date, pd.Timestamp("09:00").time()).tz_localize(session_tz)
    session_end = pd.Timestamp.combine(session_date, pd.Timestamp("13:30").time()).tz_localize(session_tz)

    last_price = quote.get("lastPrice") or quote.get("closePrice") or float(df["close"].iloc[-1])
    prev_close = quote.get("previousClose") or quote.get("referencePrice")
    change = quote.get("change")
    change_pct = quote.get("changePercent")

    if change is None and prev_close is not None:
        change = last_price - prev_close
    if change_pct is None and prev_close:
        change_pct = (change / prev_close * 100) if (change is not None and prev_close) else None

    is_up = (change or 0) >= 0
    line_color = "#dc2626" if is_up else "#16a34a"  # 台股慣例：紅漲綠跌
    fill_color = "rgba(220,38,38,0.08)" if is_up else "rgba(22,163,74,0.08)"
    sign = "+" if (change or 0) >= 0 else ""

    m1, m2, m3 = st.columns(3)
    m1.metric("加權指數", f"{last_price:,.2f}")
    m2.metric("漲跌", f"{sign}{change:,.2f}" if change is not None else "—")
    m3.metric("漲跌幅", f"{sign}{change_pct:,.2f}%" if change_pct is not None else "—")

    # 加權指數本身是四萬多點的絕對值，但當日震盪通常只有幾百點；
    # 如果用 fill="tozeroy" 會強迫 Y 軸從 0 開始，把整天的漲跌壓成貼齊頂端的一條線。
    # 這裡改用「以資料範圍為主、留一點邊界」的方式讓曲線的起伏看得出來。
    y_values = pd.concat([df["close"], pd.Series([prev_close] if prev_close is not None else [])])
    y_min, y_max = float(y_values.min()), float(y_values.max())
    y_span = max(y_max - y_min, 1.0)
    y_pad = y_span * 0.15
    y_range = [y_min - y_pad, y_max + y_pad]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["date"],
            y=df["close"],
            mode="lines",
            line=dict(color=line_color, width=2),
            fill="tozeroy",
            fillcolor=fill_color,
            name="加權指數",
            hovertemplate="%{x|%H:%M}<br>%{y:,.2f}<extra></extra>",
        )
    )
    if prev_close is not None:
        fig.add_hline(
            y=prev_close,
            line_dash="dot",
            line_color="rgba(255,255,255,0.35)",
            annotation_text="昨收",
            annotation_position="top left",
        )

    fig.update_layout(
        height=260,
        margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(
            type="date",
            range=[session_start, session_end],
            dtick=30 * 60 * 1000,  # 每 30 分鐘一格
            tickformat="%H:%M",
            showgrid=True,
            gridcolor="rgba(255,255,255,0.08)",
            tickfont=dict(size=10),
        ),
        yaxis=dict(
            showgrid=True,
            gridcolor="rgba(255,255,255,0.08)",
            tickfont=dict(size=10),
            range=y_range,
            tickformat=",.0f",
        ),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    ts = quote.get("lastUpdated") or quote.get("closeTime")
    if ts:
        try:
            ts_dt = datetime.fromtimestamp(ts / 1_000_000, TW_TZ)
            st.caption(f"資料時間：{ts_dt.strftime('%H:%M:%S')}（來源：富邦 API）")
        except Exception:
            pass


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
    filename = create_backup_filename()
    file_path = os.path.join(BACKUP_DIR, filename)
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
    if not validated:
        raise ValueError("JSON 內容為空")
    return validated

# =============================================================================
# GitHub 讀寫（stock_groups.json）
# =============================================================================
def github_repo_config():
    """monitor 這個 repo 自己的設定 (henglunlin-stock-monitor-FUBAN)。"""
    return {
        "token": get_secret_or_default("GITHUB_TOKEN", ""),
        "owner": get_secret_or_default("GITHUB_OWNER", "henglunlin"),
        "repo": get_secret_or_default("GITHUB_REPO", "henglunlin-stock-monitor-FUBAN"),
        "branch": get_secret_or_default("GITHUB_BRANCH", "main"),
    }


def scanner_repo_config():
    """
    台股掃描器 repo (stock-scanner-FUBAN) 的設定。
    precompute_trendlines.py 那邊的排程也需要讀最新的 stock_groups.json，
    所以 monitor 這邊改動股票分組時，要順便同步推一份過去，兩邊才不會兜不起來。
    預設沿用跟 monitor repo 同一組 GITHUB_TOKEN；如果這個 token 對 stock-scanner-FUBAN
    沒有寫入權限，可以在 Secrets 另外設定 SCANNER_GITHUB_TOKEN 覆蓋。
    """
    return {
        "token": get_secret_or_default("SCANNER_GITHUB_TOKEN", "") or get_secret_or_default("GITHUB_TOKEN", ""),
        "owner": get_secret_or_default("SCANNER_GITHUB_OWNER", "henglunlin"),
        "repo": get_secret_or_default("SCANNER_GITHUB_REPO", "stock-scanner-FUBAN"),
        "branch": get_secret_or_default("SCANNER_GITHUB_BRANCH", "main"),
    }


def fetch_stock_groups_from_github() -> dict:
    """
    直接從 GitHub repo 讀取最新版 stock_groups.json（走 raw.githubusercontent.com，
    公開 repo 不需要 token 就能讀）。失敗會丟出例外，由呼叫端處理提示訊息。
    """
    cfg = github_repo_config()
    url = f"https://raw.githubusercontent.com/{cfg['owner']}/{cfg['repo']}/{cfg['branch']}/stock_groups.json"
    res = requests.get(url, timeout=15)
    res.raise_for_status()
    data = res.json()
    return validate_and_normalize_group_json(data)


def upload_file_to_repo(file_bytes: bytes, github_path: str, commit_message: str, repo_cfg: dict) -> bool:
    """透過 GitHub Contents API 建立/更新一個檔案，可以指定要推到哪個 repo (repo_cfg)。"""
    token, owner, repo, branch = repo_cfg["token"], repo_cfg["owner"], repo_cfg["repo"], repo_cfg["branch"]
    if not token or not owner or not repo:
        return False

    github_path = github_path.strip("/")
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{github_path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    sha = None
    try:
        get_res = requests.get(url, headers=headers, params={"ref": branch}, timeout=15)
        if get_res.status_code == 200:
            sha = get_res.json().get("sha")

        payload = {
            "message": commit_message,
            "content": base64.b64encode(file_bytes).decode("utf-8"),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha

        put_res = requests.put(url, headers=headers, json=payload, timeout=30)
        return put_res.status_code in (200, 201)
    except Exception:
        return False


def upload_file_to_github(file_bytes: bytes, github_path: str, commit_message: str) -> bool:
    """向下相容既有呼叫端: 預設推到本 repo (henglunlin-stock-monitor-FUBAN)。"""
    return upload_file_to_repo(file_bytes, github_path, commit_message, github_repo_config())


def upload_stock_groups_to_github(groups: dict, commit_message: str = "Update stock_groups.json via monitor app") -> bool:
    """
    推送 stock_groups.json，同時推到兩個 repo:
    1. 本身這個 repo (henglunlin-stock-monitor-FUBAN)
    2. 台股掃描器 repo (stock-scanner-FUBAN)，讓 precompute_trendlines.py 那邊的
       每日排程也能讀到跟這裡一致的股票分組。
    只要「本身這個 repo」成功就視為整體成功 (回傳 True)，
    掃描器那邊推送失敗只會另外顯示警告，不會擋住本機/本repo的正常存檔流程。
    """
    content = json.dumps(groups, ensure_ascii=False, indent=2).encode("utf-8")
    ok_self = upload_file_to_repo(content, "stock_groups.json", commit_message, github_repo_config())
    ok_scanner = upload_file_to_repo(content, "stock_groups.json", commit_message, scanner_repo_config())
    if ok_self and not ok_scanner:
        st.sidebar.warning(
            "stock_groups.json 已同步到 henglunlin-stock-monitor-FUBAN，"
            "但推送到 stock-scanner-FUBAN 失敗（請確認該 repo 的 Secrets/Token 權限），不影響本機使用。"
        )
    return ok_self


def persist_stock_groups(groups: dict):
    """
    分組存檔的統一入口：一定先存本機 stock_groups.json（掃描迴圈讀的是這份），
    如果使用者有勾選「同步到 GitHub」，且 Secrets 有設定 GITHUB_TOKEN，才會額外推回 GitHub。
    """
    save_stock_groups(groups)
    if st.session_state.get("sync_groups_to_github", False):
        if upload_stock_groups_to_github(groups):
            st.sidebar.success("已同步更新到 GitHub 的 stock_groups.json。")
        else:
            st.sidebar.warning("同步 GitHub 失敗，請確認 Secrets 中的 GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO 設定。")

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
    if "tg_last_update_id" in st.session_state and st.session_state.tg_last_update_id:
        params["offset"] = st.session_state.tg_last_update_id + 1
    try:
        res = requests.get(url, params=params, timeout=3)
        if res.status_code == 200:
            data = res.json()
            if data.get("ok") and data.get("result"):
                st.sidebar.info(f"👀 偷看到 {len(data['result'])} 則新訊息")
                triggered = False
                for item in data["result"]:
                    update_id = item["update_id"]
                    st.session_state.tg_last_update_id = update_id
                    message_text = item.get("message", {}).get("text", "").strip().lower()
                    st.sidebar.write(f"💬 內容: {message_text}")
                    if message_text == "push":
                        triggered = True
                return triggered
    except Exception:
        pass
    return False

# =============================================================================
# yfinance：今日以前歷史資料
# =============================================================================
@st.cache_data(ttl=YFINANCE_HISTORY_CACHE_TTL_SEC)
def get_history_cutoff_date(today_str: str):
    """回傳「歷史資料」允許的日期上界（不含此日期）。

    平日：直接用今天的日期即可（今天的 K 線本來就還沒收，歷史資料自然只到昨天）。
    週六／週日：因為沒有新的交易日，最新一筆歷史資料（週五收盤）會被
    get_last_price() 的「當日價格」重複抓到，導致「價格」與「昨收」變成同一天、
    漲跌% 恆為 0%。此時把上界往前推到週五，讓歷史資料只到週四，
    週五那筆改由「當日價格」呈現，避免重複。
    """
    today = pd.to_datetime(today_str).date()
    weekday = today.weekday()  # Mon=0 ... Sat=5, Sun=6
    if weekday == 5:  # 週六 -> 上界為週五
        return today - timedelta(days=1)
    if weekday == 6:  # 週日 -> 上界為週五
        return today - timedelta(days=2)
    return today


def _download_stock_data_yfinance_history_cached(symbol: str, today_str: str):
    candidates = build_yfinance_candidates(symbol)
    last_error = ""
    today = get_history_cutoff_date(today_str)

    for yf_symbol in candidates:
        try:
            df = yf.download(
                yf_symbol,
                period="3mo",
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
            )
        except Exception as e:
            last_error = f"{yf_symbol}: {e}"
            continue

        if df is None or df.empty:
            last_error = f"{yf_symbol}: yfinance 無資料"
            continue

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

        df = df.reset_index()
        date_col = "Date" if "Date" in df.columns else "Datetime" if "Datetime" in df.columns else df.columns[0]
        df = df.rename(columns={date_col: "Date"})
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
        df = df.dropna(subset=["Date"])

        df = df[df["Date"].dt.date < today]

        required_cols = ["Open", "High", "Low", "Close", "Volume"]
        if not set(required_cols).issubset(df.columns):
            last_error = f"{yf_symbol}: 缺少 OHLCV 欄位"
            continue

        for col in required_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if len(df) < 26:
            last_error = f"{yf_symbol}: 歷史資料不足 {len(df)} 筆"
            continue

        return df[["Date", "Open", "High", "Low", "Close", "Volume"]].copy()

    raise ValueError(f"無法取得 yfinance 歷史資料。已嘗試：{', '.join(candidates)}。最後錯誤：{last_error}")


# =============================================================================
# twse_ohlcv.db：本機 SQLite 歷史 OHLCV 資料（表格：ohlcv_data）
# 欄位：Date, Market('上市'/'上櫃'), SecurityCode, SecurityName, Open, High, Low, Close, Volume
# =============================================================================
def symbol_to_db_market(symbol: str) -> str:
    s = str(symbol).strip().upper()
    return "上櫃" if s.endswith(".TWO") else "上市"


@st.cache_data(ttl=DB_HISTORY_CACHE_TTL_SEC)
def _download_stock_data_db_cached(symbol: str, today_str: str, include_today: bool):
    if not os.path.exists(TWSE_DB_PATH):
        raise ValueError(f"找不到資料庫檔案：{TWSE_DB_PATH}")

    code = symbol_to_code(symbol)
    market = symbol_to_db_market(symbol)
    today = get_history_cutoff_date(today_str)

    conn = sqlite3.connect(TWSE_DB_PATH)
    try:
        df = pd.read_sql_query(
            "SELECT Date, Open, High, Low, Close, Volume FROM ohlcv_data "
            "WHERE SecurityCode = ? AND Market = ? ORDER BY Date ASC",
            conn,
            params=(code, market),
        )
    finally:
        conn.close()

    if df is None or df.empty:
        raise ValueError(f"twse_ohlcv.db 無 {symbol}（代碼 {code}，{market}）資料")

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])

    if not include_today:
        df = df[df["Date"].dt.date < today]

    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    for col in required_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])

    if len(df) < 26:
        raise ValueError(f"twse_ohlcv.db 資料不足（{symbol} 僅 {len(df)} 筆）")

    return df[["Date", "Open", "High", "Low", "Close", "Volume"]].sort_values("Date").reset_index(drop=True)


@st.cache_data(ttl=DB_LATEST_PRICE_CACHE_TTL_SEC)
def get_db_latest_price(symbol: str):
    """從 twse_ohlcv.db 取得該股票資料庫中「最新一筆」的收盤價與日期（用於『全部由 DB 讀取』模式的當日價格）。"""
    if not os.path.exists(TWSE_DB_PATH):
        raise ValueError(f"找不到資料庫檔案：{TWSE_DB_PATH}")

    code = symbol_to_code(symbol)
    market = symbol_to_db_market(symbol)

    conn = sqlite3.connect(TWSE_DB_PATH)
    try:
        row = conn.execute(
            "SELECT Date, Close FROM ohlcv_data WHERE SecurityCode = ? AND Market = ? "
            "ORDER BY Date DESC LIMIT 1",
            (code, market),
        ).fetchone()
    finally:
        conn.close()

    if not row or row[1] is None:
        raise ValueError(f"twse_ohlcv.db 無 {symbol}（代碼 {code}，{market}）最新價格")

    date_str, close = row
    return float(close), str(date_str)


def download_stock_data(symbol):
    today_str = datetime.now(TW_TZ).strftime("%Y-%m-%d")

    if st.session_state.get("post_market_enabled", False):
        source = st.session_state.get("post_market_source", "db")
        if source == "db":
            return _download_stock_data_db_cached(symbol, today_str, include_today=False)
        return _download_stock_data_yfinance_history_cached(symbol, today_str)

    source = st.session_state.get("history_source", "db")
    if source == "db":
        return _download_stock_data_db_cached(symbol, today_str, include_today=False)
    return _download_stock_data_yfinance_history_cached(symbol, today_str)

def normalize_ohlc(df):
    if df is None or df.empty:
        return pd.DataFrame()
    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    if set(required_cols).issubset(df.columns):
        keep_cols = ["Date"] + required_cols if "Date" in df.columns else required_cols
        return df[keep_cols].copy()
    return pd.DataFrame()


def is_fubon_realtime_time():
    now = datetime.now(TW_TZ).time()
    start = datetime.strptime("09:00", "%H:%M").time()
    end = datetime.strptime("13:30", "%H:%M").time()
    return start <= now < end


def parse_price_value(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if isinstance(value, dict):
        for key in ["raw", "fmt", "value"]:
            parsed = parse_price_value(value.get(key))
            if parsed is not None:
                return parsed
        return None
    try:
        text_val = str(value).strip().replace(",", "")
        if not text_val or text_val in ["-", "--", "None", "nan"]:
            return None
        return float(text_val)
    except Exception:
        return None


def get_yfinance_fast_info_price(symbol: str):
    candidates = [str(symbol).strip().upper()] + [
        s for s in build_yfinance_candidates(symbol)
        if s != str(symbol).strip().upper()
    ]
    seen = set()
    last_error = ""
    for yf_symbol in candidates:
        if not yf_symbol or yf_symbol in seen:
            continue
        seen.add(yf_symbol)
        try:
            ticker = yf.Ticker(yf_symbol)
            price = ticker.fast_info.get("last_price", None)
            if price is not None and pd.notna(price):
                return float(price), yf_symbol
        except Exception as e:
            last_error = f"{yf_symbol}: {e}"
            continue
    raise ValueError(f"yfinance fast_info 無法取得 {symbol} 價格。最後錯誤：{last_error}")


@st.cache_data(ttl=30)
def get_yahoo_tw_quote_price(symbol: str):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://tw.stock.yahoo.com/",
    }
    last_error = ""
    for yahoo_symbol in build_yfinance_candidates(symbol):
        url = f"https://tw.stock.yahoo.com/_td-stock/api/resource/StockServices.stockList;symbols={yahoo_symbol}"
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code != 200:
                last_error = f"{yahoo_symbol}: HTTP {res.status_code}"
                continue
            raw_text = res.text.strip()
            if raw_text.startswith(")]}'"):
                raw_text = raw_text.split("\n", 1)[-1]
            payload = json.loads(raw_text)
        except Exception as e:
            last_error = f"{yahoo_symbol}: {e}"
            continue
        items = payload if isinstance(payload, list) else payload.get("data", []) if isinstance(payload, dict) else []
        if isinstance(items, dict):
            items = [items]
        for item in items:
            if not isinstance(item, dict):
                continue
            price_keys = ["regularMarketPrice", "price", "lastPrice", "tradePrice", "close", "closePrice", "latestPrice"]
            for key in price_keys:
                price = parse_price_value(item.get(key))
                if price is not None:
                    return float(price), yahoo_symbol
            for value in item.values():
                if isinstance(value, dict):
                    for key in price_keys:
                        price = parse_price_value(value.get(key))
                        if price is not None:
                            return float(price), yahoo_symbol
        last_error = f"{yahoo_symbol}: 找不到可用價格欄位"
    raise ValueError(f"Yahoo TW 無法取得 {symbol} 價格。最後錯誤：{last_error}")


@st.cache_data(ttl=30)
def get_yfinance_latest_daily_close(symbol: str):
    last_error = ""
    for yf_symbol in build_yfinance_candidates(symbol):
        try:
            daily_df = yf.download(yf_symbol, period="10d", interval="1d", auto_adjust=True, progress=False, threads=False)
        except Exception as e:
            last_error = f"{yf_symbol}: {e}"
            continue
        if daily_df is None or daily_df.empty:
            last_error = f"{yf_symbol}: daily 無資料"
            continue
        if isinstance(daily_df.columns, pd.MultiIndex):
            daily_df.columns = [c[0] if isinstance(c, tuple) else c for c in daily_df.columns]
        if "Close" not in daily_df.columns:
            last_error = f"{yf_symbol}: daily 缺少 Close 欄位"
            continue
        daily_df = daily_df.reset_index()
        date_col = "Date" if "Date" in daily_df.columns else "Datetime" if "Datetime" in daily_df.columns else daily_df.columns[0]
        daily_df = daily_df.rename(columns={date_col: "Date"})
        daily_df["Date"] = pd.to_datetime(daily_df["Date"], errors="coerce")
        daily_df["Close"] = pd.to_numeric(daily_df["Close"], errors="coerce")
        daily_df = daily_df.dropna(subset=["Date", "Close"]).sort_values("Date")
        if daily_df.empty:
            last_error = f"{yf_symbol}: daily Close 皆為空"
            continue
        last_row = daily_df.iloc[-1]
        return float(last_row["Close"]), pd.to_datetime(last_row["Date"]).date(), yf_symbol
    raise ValueError(f"yfinance daily 無法取得 {symbol} 最新收盤價。最後錯誤：{last_error}")


def after_1330_price_logic(symbol, df, forced=False):
    last_hist_close = None
    last_hist_date = None
    if df is not None and not df.empty and "Close" in df.columns:
        try:
            last_hist_close = float(df["Close"].iloc[-1])
        except Exception:
            last_hist_close = None
        try:
            if "Date" in df.columns:
                last_hist_date = pd.to_datetime(df["Date"].iloc[-1]).date()
        except Exception:
            last_hist_date = None
    fast_price = None
    try:
        fast_price, _ = get_yfinance_fast_info_price(symbol)
    except Exception:
        fast_price = None
    if fast_price is not None and pd.notna(fast_price):
        if last_hist_close is None or abs(float(fast_price) - last_hist_close) > 1e-9:
            return float(fast_price), "Forced 13:30 yfinance fast_info" if forced else "yfinance after 13:30"
    try:
        yahoo_price, _ = get_yahoo_tw_quote_price(symbol)
        if yahoo_price is not None and pd.notna(yahoo_price):
            return float(yahoo_price), "Forced 13:30 Yahoo TW" if forced else "Yahoo TW after 13:30"
    except Exception:
        pass
    try:
        daily_close, daily_date, _ = get_yfinance_latest_daily_close(symbol)
        if daily_close is not None and pd.notna(daily_close):
            if last_hist_date is None or daily_date > last_hist_date:
                return float(daily_close), "Forced 13:30 yfinance daily" if forced else "yfinance daily after 13:30"
    except Exception:
        pass
    if fast_price is not None and pd.notna(fast_price):
        return float(fast_price), "Forced 13:30 yfinance stale fast_info" if forced else "yfinance stale fast_info after 13:30"
    if last_hist_close is not None:
        return last_hist_close, "Forced 13:30 history fallback" if forced else "history after 13:30"
    raise ValueError("無法取得 13:30 後價格")


def get_last_price(symbol, df, manager=None):
    if st.session_state.get("post_market_enabled", False):
        source = st.session_state.get("post_market_source", "db")
        today_str = datetime.now(TW_TZ).strftime("%Y-%m-%d")
        if source == "db":
            db_price, db_date_str = get_db_latest_price(symbol)
            source_label = "TWSE DB（今日）" if db_date_str == today_str else f"TWSE DB（最新收盤 {db_date_str}）"
            return float(db_price), source_label
        daily_close, daily_date, _ = get_yfinance_latest_daily_close(symbol)
        source_label = "yfinance（今日收盤）" if str(daily_date) == today_str else f"yfinance（最新收盤 {daily_date}）"
        return float(daily_close), source_label

    realtime_source = st.session_state.get("realtime_source", "fubon")
    if realtime_source == "yfinance":
        return after_1330_price_logic(symbol, df, forced=True)

    use_fubon_ws = is_fubon_realtime_time()
    if manager is not None and use_fubon_ws:
        ws_price = manager.get_price(symbol)
        if ws_price is not None and pd.notna(ws_price):
            return float(ws_price), "Fubon WebSocket trades"
    if use_fubon_ws:
        try:
            yf_price, _ = get_yfinance_fast_info_price(symbol)
            return float(yf_price), "yfinance fallback"
        except Exception:
            pass
        if df is not None and not df.empty and "Close" in df.columns:
            return float(df["Close"].iloc[-1]), "history fallback"
        raise ValueError("無法取得即時價格")
    return after_1330_price_logic(symbol, df, forced=False)

# =============================================================================
# 股票名稱 / 查詢
# =============================================================================
@st.cache_data(ttl=86400)
def load_stock_name_map(file_path: str = STOCK_NAME_FILE) -> dict:
    name_map = {}
    if not os.path.exists(file_path):
        return name_map
    with open(file_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            line = line.replace("\ufeff", "").replace("\u3000", "")
            if "\t" in line:
                parts = [p.strip() for p in line.split("\t") if p.strip()]
                if len(parts) >= 2:
                    symbol = parts[0].upper()
                    name = parts[1].strip()
                    name_map[symbol] = name
                    name_map[symbol_to_code(symbol)] = name
                    continue
            m = re.match(r"^([^\s]+)\s+(.+)$", line)
            if m:
                symbol = m.group(1).strip().upper()
                name = m.group(2).strip()
                name_map[symbol] = name
                name_map[symbol_to_code(symbol)] = name
    return name_map


@st.cache_data(ttl=86400)
def get_stock_name(symbol: str) -> str:
    name_map = load_stock_name_map(STOCK_NAME_FILE)
    code = symbol_to_code(symbol)
    if symbol in name_map:
        return name_map[symbol]
    if code in name_map:
        return name_map[code]
    try:
        for yf_symbol in build_yfinance_candidates(symbol):
            ticker = yf.Ticker(yf_symbol)
            info = {}
            try:
                info = ticker.get_info()
            except Exception:
                try:
                    info = ticker.info
                except Exception:
                    info = {}
            for key in ["shortName", "longName", "displayName", "name"]:
                val = info.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    except Exception:
        pass
    return code


@st.cache_data(ttl=86400)
def load_stock_lookup_maps(file_path: str = STOCK_NAME_FILE) -> dict:
    code_to_name = {}
    code_to_symbol = {}
    name_to_symbol = {}
    if not os.path.exists(file_path):
        return {"code_to_name": code_to_name, "code_to_symbol": code_to_symbol, "name_to_symbol": name_to_symbol}
    with open(file_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            line = line.replace("\ufeff", "").replace("\u3000", " ").strip()
            if "\t" in line:
                parts = [p.strip() for p in line.split("\t") if p.strip()]
            else:
                m = re.match(r"^([^\s]+)\s+(.+)$", line)
                parts = [m.group(1).strip(), m.group(2).strip()] if m else []
            if len(parts) < 2:
                continue
            raw_symbol = parts[0].upper()
            stock_name = parts[1].strip()
            symbol = normalize_lookup_symbol(raw_symbol)
            code = symbol_to_code(symbol)
            if not code or not stock_name:
                continue
            code_to_name[code] = stock_name
            code_to_symbol[code] = symbol
            name_to_symbol[stock_name] = symbol
            name_to_symbol[stock_name.replace(" ", "")] = symbol
    return {"code_to_name": code_to_name, "code_to_symbol": code_to_symbol, "name_to_symbol": name_to_symbol}


def resolve_stock_query(input_text: str):
    q_raw = str(input_text).strip()
    if not q_raw:
        return None, None, None
    lookup = load_stock_lookup_maps(STOCK_NAME_FILE)
    code_to_name = lookup.get("code_to_name", {})
    code_to_symbol = lookup.get("code_to_symbol", {})
    name_to_symbol = lookup.get("name_to_symbol", {})
    q_upper = q_raw.upper()
    if "." in q_upper:
        symbol = q_upper
        code = symbol_to_code(symbol)
        return symbol, code_to_name.get(code) or get_stock_name(symbol), "ticker"
    if q_upper.isdigit():
        code = q_upper
        symbol = code_to_symbol.get(code) or normalize_symbol_quick(code)
        return symbol, code_to_name.get(code) or get_stock_name(symbol), "code"
    symbol = name_to_symbol.get(q_raw) or name_to_symbol.get(q_raw.replace(" ", ""))
    if symbol:
        code = symbol_to_code(symbol)
        return symbol, code_to_name.get(code) or q_raw, "name"
    compact_query = q_raw.replace(" ", "")
    if compact_query:
        for stock_name, candidate_symbol in name_to_symbol.items():
            if compact_query in stock_name.replace(" ", ""):
                code = symbol_to_code(candidate_symbol)
                return candidate_symbol, code_to_name.get(code) or stock_name, "name_partial"
    symbol = normalize_symbol_quick(q_raw)
    if symbol:
        code = symbol_to_code(symbol)
        return symbol, code_to_name.get(code), "fallback"
    return None, None, None

# =============================================================================
# 盤中最低價追蹤（供跳空訊號使用）
# =============================================================================
# 只有「真正的即時成交價」才可以拿來更新/建立當天的最低價紀錄。
# 富邦 WebSocket 尚未連上、或該股票今天還沒有任何一筆 WS 成交進來時，
# get_last_price() 會 fallback 到 yfinance / 歷史收盤價（甚至直接就是「昨收」）；
# 這種價格不代表今天真的有成交在那個價位，如果拿去更新追蹤最低價，
# 會把追蹤值錯誤地押在昨收附近，之後就算股票鎖漲停一整天，
# session_low 也永遠低於昨天最高價，導致跳空訊號整天都不會觸發
# （例如 2303 開盤直接鎖漲停在121，但因為第一次抓值時 WS 還沒送出成交，
# fallback 抓到「history fallback」＝昨收110，把110記成當天最低價，
# 之後即使真的收到121的成交價，110仍是全天最小值，跳空條件永遠不成立）。
TRUSTED_INTRADAY_LOW_SOURCES = {"Fubon WebSocket trades", "Forced WebSocket"}


def update_intraday_low(symbol: str, price_val: float, now_dt, price_source: str = None) -> float:
    """
    追蹤每檔股票「今天」實際看過的最低成交價。

    富邦 WebSocket 逐筆成交訊息只有單一成交價，沒有當日最低價欄位；如果每次
    刷新都只拿「這一筆」價格去判斷是否跳空，價格只要稍微上下跳動，跳空訊號
    就會一直忽有忽無。這裡用 st.session_state 把每檔股票「今天」看過的最低價
    記錄下來，換日時自動重置，讓跳空判斷有穩定、連續的依據。

    price_source 為 None，或不在 TRUSTED_INTRADAY_LOW_SOURCES 內時（例如
    "yfinance fallback"、"history fallback" 這類非即時成交來源），這筆價格
    不會被寫入/拉低追蹤值，避免用不可信的價格污染整天的最低價紀錄；
    若當天目前為止都還沒有任何可信紀錄，則暫時原樣回傳這筆價格，
    等真正的即時成交進來後追蹤值會自動修正。
    """
    if "intraday_low_tracker" not in st.session_state:
        st.session_state.intraday_low_tracker = {}
    tracker = st.session_state.intraday_low_tracker
    today_str = now_dt.strftime("%Y-%m-%d")
    record = tracker.get(symbol)
    is_trusted = price_source is None or price_source in TRUSTED_INTRADAY_LOW_SOURCES

    if pd.isna(price_val):
        return record["low"] if (record is not None and record.get("date") == today_str) else price_val

    if not is_trusted:
        # 不可信來源：只讀不寫，若當天還沒有任何可信紀錄則先暫時回傳這筆價格本身。
        if record is not None and record.get("date") == today_str:
            return record["low"]
        return price_val

    if record is None or record.get("date") != today_str:
        tracker[symbol] = {"date": today_str, "low": price_val}
        return price_val
    if price_val < record["low"]:
        record["low"] = price_val
    return record["low"]


def update_intraday_high(symbol: str, price_val: float, now_dt, price_source: str = None) -> float:
    """與 update_intraday_low() 對稱：追蹤每檔股票「今天」實際看過的最高成交價（供 signal_module 訊號使用）。"""
    if "intraday_high_tracker" not in st.session_state:
        st.session_state.intraday_high_tracker = {}
    tracker = st.session_state.intraday_high_tracker
    today_str = now_dt.strftime("%Y-%m-%d")
    record = tracker.get(symbol)
    is_trusted = price_source is None or price_source in TRUSTED_INTRADAY_LOW_SOURCES

    if pd.isna(price_val):
        return record["high"] if (record is not None and record.get("date") == today_str) else price_val

    if not is_trusted:
        if record is not None and record.get("date") == today_str:
            return record["high"]
        return price_val

    if record is None or record.get("date") != today_str:
        tracker[symbol] = {"date": today_str, "high": price_val}
        return price_val
    if price_val > record["high"]:
        record["high"] = price_val
    return record["high"]


# =============================================================================
# 指標計算
# =============================================================================
def compute_indicators(df, price):
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

    yesterday_close = float(close.iloc[-1])
    if pd.isna(yesterday_close) or yesterday_close == 0:
        raise ValueError("昨收資料異常")

    price_val = float(price)
    change_pct = float((price_val / yesterday_close - 1) * 100)

    today_row = pd.DataFrame([{
        "Date": pd.Timestamp(datetime.now(TW_TZ).date()),
        "Open": price_val,
        "High": price_val,
        "Low": price_val,
        "Close": price_val,
        "Volume": 0,
    }])
    calc_df = pd.concat([calc_df, today_row], ignore_index=True)
    close = pd.to_numeric(calc_df["Close"].squeeze(), errors="coerce")
    low = pd.to_numeric(calc_df["Low"].squeeze(), errors="coerce")
    high = pd.to_numeric(calc_df["High"].squeeze(), errors="coerce")

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
    k = rsv.ewm(alpha=1/3, adjust=False).mean()
    d = k.ewm(alpha=1/3, adjust=False).mean()
    if len(k.dropna()) < 2 or len(d.dropna()) < 2:
        raise ValueError("KD 計算資料不足")

    k_t = float(k.iloc[-1])
    d_t = float(d.iloc[-1])

    # KD黃金交叉/跳空訊號的判斷已經移交給 signal_module（見下方「訊號模組銜接」，
    # 對應主表格的「買賣訊號」欄位），這裡只保留 K值/D值/MA位置/MA排列 供顯示用。
    return {
        "price": round(price_val, 2),
        "pct": round(change_pct, 2),
        "yesterday_close": round(yesterday_close, 2),
        "ma_range": ma_range,
        "ma_trend": ma_trend,
        "k": round(k_t, 1),
        "d": round(d_t, 1),
    }

# =============================================================================
# 訊號模組 (signal_module) 銜接
# =============================================================================
# 沿用跟「台股掃描器」repo 相同的 signal_module/（KD高腳、三白兵、布林縮窄突破...
# 等 17 個可編輯訊號檔案）。這裡把它接到本監控面板既有的即時資料流程：
# 用歷史日K + 「今天」即時開高低收組成當日這一根K棒，餵給 signal_module 算指標、跑訊號，
# 再依照「優先等級」規則收斂成單一「買賣訊號」欄位。
from signal_module import module_loader
from signal_module.base import SIGNAL_REGISTRY, SignalContext as ModuleSignalContext
from signal_module.indicators import add_indicators as _sm_add_indicators

if not SIGNAL_REGISTRY:
    module_loader.load_default_signal_modules()

# 訊號優先等級：數字越小越重要（1 > 2 > 3）。同一天如果同等級的訊號一起觸發，就一起顯示；
# 等級不同時只顯示等級數字最小（最重要）的那些。
# key 對應到 signal_module 各檔案 register_signal() 裡的 label。
# 不在下面清單內的訊號（例如漲幅達標）預設視為最低優先等級 3，可自行調整。
SIGNAL_PRIORITY = {
    "布林縮窄突破": 1,
    "反向島狀": 1,
    "3K反轉": 2,
    "巧妙點": 2,
    "雙跳空": 2,
    "雙漲停": 2,
    "島狀反轉": 2,
    "KD高腳": 2,
    "跌停": 2,
    "單跳空": 2,
    "周1K": 2,
    "廣義下降三法": 3,
    "漲停": 3,
    "移動停利": 3,
    "廣義上升三法": 3,
    "三白兵": 3,
}
SIGNAL_PRIORITY_DEFAULT = 3


def get_signal_registry():
    return SIGNAL_REGISTRY


def _prepare_signal_dataframe(df: pd.DataFrame, open_val: float, high_val: float, low_val: float, close_val: float) -> pd.DataFrame:
    """
    用歷史日K + 「今天」即時開高低收，組成 signal_module 需要的格式：
    index = Date字串、由舊到新排序，並附上 K/D/MA/Bias/BBand 等技術指標欄位。

    重要修正 (2026-08，第二版)：
    第一版的問題：只有在「資料庫最後一筆的日期」跟「今天的日曆日期」完全相等時，
    才會把即時追蹤到的高低跟真實資料取聯集；一旦遇到週末/假日重新整理頁面
    (例如今天週日、資料庫最後一筆是上週五)，這個條件永遠不成立，
    程式就會退回舊行為：無中生有造一根「週日」的K棒(開高低收全部等於同一個即時價，
    因為週末沒開盤、輪詢到的價格通常就是停在最後收盤價)，疊在真實資料後面，
    然後訊號模組就會拿這根假的一字線去跟真實的前一交易日比較，一樣會產生誤判。

    真正需要判斷的不是「日期是否等於今天」，而是「現在是不是一個可能有新交易日
    尚未同步進資料庫的平日」。所以改成：
      - 如果「現在」是平日(週一~週五)，且資料庫最後一筆的日期就是今天
        → 今天已經有真實資料，跟即時值取聯集(維持第一版的修正)。
      - 如果「現在」不是平日(週六/週日)，或資料庫最後一筆本來就還不是「今天」
        但也還沒到「新的交易日」該登場的時候 → 不要無中生有造新的一天，
        直接把即時值併入資料庫「最後一個交易日」那一筆，沿用該筆的真實日期繼續當
        scan_date(而不是用假的週末日期)。
      - 只有「平日」且資料庫最後一筆日期還停留在更早之前(代表今天這個交易日
        還沒同步進資料庫，這是真正的盤中情境) → 才新增一筆「今天」。

    ⚠️ 已知限制：目前只用「是否為週六/週日」判斷是否為交易日，沒有內建台股國定假日
    行事曆。如果「今天」剛好是平日的國定假日(股市休市)，還是可能會誤判成「新交易日」
    而多出一根假的一字線K棒。如果需要完全避免，之後可以另外維護一份台股休市日期清單。
    """
    work = df.copy()
    if "Date" not in work.columns:
        work = work.reset_index().rename(columns={work.reset_index().columns[0]: "Date"})
    work["Date"] = pd.to_datetime(work["Date"], errors="coerce")
    work = work.dropna(subset=["Date"]).sort_values("Date").reset_index(drop=True)
    if work.empty:
        raise ValueError("下載資料為空")

    now_dt = datetime.now(TW_TZ)
    today_ts = pd.Timestamp(now_dt.date())
    is_weekday = now_dt.weekday() < 5  # 0=一 ... 4=五, 5=六, 6=日
    last_date = work["Date"].iloc[-1].normalize()

    should_merge_into_last_row = (last_date == today_ts) or (not is_weekday)

    if should_merge_into_last_row:
        real_today = work.iloc[-1]
        real_date = work["Date"].iloc[-1]
        candidate_highs = [v for v in [real_today.get("High"), high_val] if pd.notna(v)]
        candidate_lows = [v for v in [real_today.get("Low"), low_val] if pd.notna(v)]
        real_open = real_today.get("Open")

        merged_high = max(candidate_highs) if candidate_highs else high_val
        merged_low = min(candidate_lows) if candidate_lows else low_val
        merged_open = real_open if pd.notna(real_open) else open_val

        work = work.iloc[:-1]
        today_ts = real_date  # 沿用資料庫裡「真實」的交易日日期，不要用假的週末/日曆日期
        open_val, high_val, low_val = merged_open, merged_high, merged_low
        # close_val 維持傳入的即時價：盤中即時反映，非交易時間通常就等於當天實際收盤，不受影響。

    today_row = pd.DataFrame([{
        "Date": today_ts, "Open": open_val, "High": high_val, "Low": low_val, "Close": close_val, "Volume": 0,
    }])
    work = pd.concat([work[["Date", "Open", "High", "Low", "Close", "Volume"]], today_row], ignore_index=True)

    work = work.set_index(work["Date"].dt.strftime("%Y-%m-%d"))[["Open", "High", "Low", "Close", "Volume"]]
    work.index.name = "Date"
    work = _sm_add_indicators(work)
    return work


def run_stock_signals(symbol, name, df, open_val, high_val, low_val, close_val, rise_threshold=5.0):
    """
    對單一股票跑過全部已註冊訊號。
    回傳 (hit_list, display_text)：
      hit_list：依優先等級排序的命中訊號清單 [{"label","kind","priority","detail"}, ...]
      display_text：套用「優先等級」規則後的顯示文字
                     （數字越小越重要；同一天同等級的訊號一起觸發就一起顯示）
    """
    try:
        df_ind = _prepare_signal_dataframe(df, open_val, high_val, low_val, close_val)
    except Exception:
        return [], "-"

    scan_date = df_ind.index[-1]
    ctx = ModuleSignalContext(
        code=symbol, name=name, df=df_ind, scan_date=scan_date,
        params={"rise_threshold": rise_threshold},
    )

    hit_list = []
    for key, cfg in SIGNAL_REGISTRY.items():
        try:
            result = cfg["func"](ctx)
        except Exception:
            continue
        if getattr(result, "hit", False):
            label = cfg["label"]
            hit_list.append({
                "label": label,
                "kind": cfg.get("kind", "buy"),
                "priority": SIGNAL_PRIORITY.get(label, SIGNAL_PRIORITY_DEFAULT),
                "detail": result.detail,
            })

    if not hit_list:
        return [], "-"

    hit_list.sort(key=lambda h: h["priority"])
    top_priority = hit_list[0]["priority"]
    top_hits = [h for h in hit_list if h["priority"] == top_priority]
    display_text = "、".join(f"{h['label']}({'買' if h['kind'] == 'buy' else '賣'})" for h in top_hits)
    return hit_list, display_text



# =============================================================================
# UI 格式
# =============================================================================
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


def format_signal(val):
    """買賣訊號欄位上色：買進偏紅、賣出偏綠（與漲跌%的紅漲綠跌配色一致）。"""
    if not val or val == "-":
        return "-"
    if "(賣)" in val:
        return f"🟢 {val}"
    if "(買)" in val:
        return f"🔴 {val}"
    return val


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


# =============================================================================
# Excel 匯出（把目前畫面上每個分組的表格彙整成一份 Excel）
# =============================================================================
EXCEL_EXPORT_COLUMNS = [
    "代碼", "股票名稱", "價格", "昨收", "漲跌%", "MA位置", "MA排列",
    "K值", "D值", "買賣訊號", "價格來源",
]


def _contains_cjk(text) -> bool:
    if text is None:
        return False
    s = str(text)
    return any(
        ("\u4e00" <= ch <= "\u9fff") or ("\u3400" <= ch <= "\u4dbf") or ("\uf900" <= ch <= "\ufaff")
        for ch in s
    )


def _apply_excel_fonts(workbook):
    """中英文分開套字型：中文用微軟正黑體，英數用 Calibri，Excel 開起來比較好看。"""
    from openpyxl.styles import Font
    for worksheet in workbook.worksheets:
        for row in worksheet.iter_rows():
            for cell in row:
                if cell.value is None:
                    cell.font = Font(name="Calibri")
                elif _contains_cjk(cell.value):
                    cell.font = Font(name="Microsoft JhengHei")
                else:
                    cell.font = Font(name="Calibri")


def _safe_excel_sheet_name(name: str, used_names: set) -> str:
    """
    Excel 分頁名稱規則：不能超過31字元、不能包含 : \\ / ? * [ ]、不能重複、不能是空字串。
    分組名稱如果剛好違反這些規則(例如中文名稱剛好超長、或兩個分組去掉特殊字元後撞名)，
    這裡會自動截短/補號碼，確保一定能成功寫入 Excel。
    """
    cleaned = re.sub(r'[:\\/?*\[\]]', "_", str(name)).strip() or "分組"
    cleaned = cleaned[:31]
    candidate = cleaned
    suffix = 1
    while candidate in used_names:
        suffix_str = f"_{suffix}"
        candidate = cleaned[: 31 - len(suffix_str)] + suffix_str
        suffix += 1
    used_names.add(candidate)
    return candidate


def build_monitor_excel_bytes(group_tables: dict) -> bytes:
    """
    把目前畫面上每個分組的監控表格彙整成一份 Excel：
    - 「全部彙總」分頁：所有分組合併在一起，多一欄「分類」方便篩選/排序
    - 其餘每個分組各自一個分頁，跟畫面上顯示的分組一一對應
    數值欄位(漲跌%/K值/D值)維持原始數字、買賣訊號不含emoji，方便在 Excel 裡排序/篩選/畫圖，
    跟畫面上為了好讀而加的顏色/emoji裝飾分開處理。
    """
    from io import BytesIO

    output = BytesIO()
    used_sheet_names = set()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        all_rows = []
        for group_name, info in group_tables.items():
            for row in info.get("raw_rows", []):
                merged = {"分類": group_name}
                merged.update({col: row.get(col) for col in EXCEL_EXPORT_COLUMNS})
                all_rows.append(merged)

        summary_columns = ["分類"] + EXCEL_EXPORT_COLUMNS
        summary_df = pd.DataFrame(all_rows, columns=summary_columns) if all_rows else pd.DataFrame(columns=summary_columns)
        summary_sheet_name = _safe_excel_sheet_name("全部彙總", used_sheet_names)
        summary_df.to_excel(writer, sheet_name=summary_sheet_name, index=False)

        for group_name, info in group_tables.items():
            raw_rows = info.get("raw_rows", [])
            group_df = (
                pd.DataFrame(raw_rows, columns=EXCEL_EXPORT_COLUMNS)
                if raw_rows else pd.DataFrame(columns=EXCEL_EXPORT_COLUMNS)
            )
            sheet_name = _safe_excel_sheet_name(group_name, used_sheet_names)
            group_df.to_excel(writer, sheet_name=sheet_name, index=False)

        _apply_excel_fonts(writer.book)

    output.seek(0)
    return output.getvalue()


def render_summary_dashboard(group_up_summary, rise_threshold):
    st.markdown('<div id="dashboard-top" style="scroll-margin-top: 90px;"></div>', unsafe_allow_html=True)
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
            bg_color = "#fff1f0"; border_color = "#ff7875"; accent_color = "#cf1322"
        elif hit_ratio > 0:
            bg_color = "#fff7e6"; border_color = "#ffa940"; accent_color = "#d46b08"
        else:
            bg_color = "#f6ffed"; border_color = "#95de64"; accent_color = "#389e0d"
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
    st.session_state.auto_refresh_enabled = False
if "refresh_sec" not in st.session_state:
    st.session_state.refresh_sec = REFRESH_SEC
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
if "realtime_source" not in st.session_state:
    st.session_state.realtime_source = "fubon"       # 即時資料（當日資料）：fubon(預設) / yfinance
if "history_source" not in st.session_state:
    st.session_state.history_source = "db"            # 歷史資料（當日以前的資料）：db(預設) / yfinance
if "post_market_enabled" not in st.session_state:
    st.session_state.post_market_enabled = False       # 盤後資料（當日+歷史資料）模式開關
if "post_market_source" not in st.session_state:
    st.session_state.post_market_source = "db"          # 盤後資料來源：db(預設) / yfinance
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
if "intraday_low_tracker" not in st.session_state:
    st.session_state.intraday_low_tracker = {}
if "intraday_high_tracker" not in st.session_state:
    st.session_state.intraday_high_tracker = {}
if "tg_last_update_id" not in st.session_state:
    st.session_state.tg_last_update_id = None
if "_next_selected_group" in st.session_state:
    pending_group = st.session_state._next_selected_group
    del st.session_state._next_selected_group
    if pending_group in st.session_state.stock_groups:
        st.session_state.selected_group_editor = pending_group
        st.session_state.rename_group_input = pending_group
        st.session_state.symbols_text_area = "\n".join(st.session_state.stock_groups.get(pending_group, []))
if "_clear_quick_add_symbol_input" in st.session_state:
    del st.session_state._clear_quick_add_symbol_input
    st.session_state.quick_add_symbol_input = ""
if "_quick_add_success_message" in st.session_state:
    st.toast(st.session_state._quick_add_success_message)
    del st.session_state._quick_add_success_message


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
        if group_names:
            selected_group = group_names[0]
            st.session_state.selected_group_editor = selected_group
        else:
            selected_group = ""
    st.session_state.rename_group_input = selected_group
    st.session_state.symbols_text_area = "\n".join(groups.get(selected_group, []))
    st.session_state.editing_mode = False

# =============================================================================
# 富邦登入 UI
# =============================================================================
def get_fubon_pfx_base64():
    try:
        return st.secrets["fubon"]["pfx_base64"]
    except Exception:
        return ""


def render_fubon_login():
    st.sidebar.markdown("## 🔑 富邦 WebSocket 即時價")
    manager = st.session_state.fubon_manager
    status = manager.get_status()

    if st.sidebar.button("清除富邦連線狀態", width="stretch"):
        st.session_state.fubon_manager = FubonRealtimeManager()
        st.session_state.fubon_logged_in = False
        st.session_state.pop("fubon_login_time", None)
        st.rerun()

    if FubonSDK is None:
        st.sidebar.warning("富邦 SDK 未載入，當日價格會使用 yfinance fallback。")
        return

    if st.session_state.fubon_logged_in:
        st.sidebar.success("✅ 富邦 WebSocket 已連線")
        st.sidebar.caption(f"已訂閱：{status['subscribed_count']} 檔")
        if status["last_message_at"]:
            st.sidebar.caption(f"最後資料：{status['last_message_at'].strftime('%H:%M:%S')}")
        if status["error"]:
            st.sidebar.warning(status["error"])
        if st.sidebar.button("登出 / 重新連線富邦", width="stretch"):
            st.session_state.fubon_manager = FubonRealtimeManager()
            st.session_state.fubon_logged_in = False
            st.session_state.pop("fubon_login_time", None)
            st.rerun()
        return

    pfx_base64 = get_fubon_pfx_base64()
    if not pfx_base64:
        st.sidebar.warning("未設定 st.secrets['fubon']['pfx_base64']，當日價格會使用 yfinance fallback。")
        return

    with st.sidebar.expander("富邦登入", expanded=False):
        f_id = st.text_input("身分證字號", key="fubon_id_input")
        f_pw = st.text_input("富邦登入密碼", key="fubon_pw_input", type="password")
        f_cert_pw = st.text_input("憑證密碼", key="fubon_cert_pw_input", type="password")
        if st.button("連線富邦 WebSocket", width="stretch"):
            if not f_id or not f_pw or not f_cert_pw:
                st.warning("請填寫完整登入資訊")
            else:
                try:
                    new_manager = FubonRealtimeManager()
                    with st.spinner("連線富邦 WebSocket 中..."):
                        new_manager.login(f_id, f_pw, f_cert_pw, pfx_base64)
                    st.session_state.fubon_manager = new_manager
                    st.session_state.fubon_logged_in = True
                    st.session_state.fubon_login_time = datetime.now(TW_TZ)
                    st.success("富邦 WebSocket 連線成功")
                    st.rerun()
                except Exception as e:
                    st.session_state.fubon_manager = FubonRealtimeManager()
                    st.session_state.fubon_logged_in = False
                    st.session_state.pop("fubon_login_time", None)
                    st.error(f"富邦登入失敗：{e}")
                    st.exception(e)

# =============================================================================
# 分組 UI
# =============================================================================
def render_group_editor_lock():
    st.sidebar.markdown("## 🔐 分組編輯鎖")
    if st.session_state.group_editor_unlocked:
        st.sidebar.success("已解鎖，可編輯股票分組")
        st.sidebar.info("為避免編輯中被重刷，分組編輯解鎖時會暫停自動更新")
        if st.sidebar.button("鎖定編輯", key="lock_group_editor_btn", width="stretch"):
            st.session_state.group_editor_unlocked = False
            leave_edit_mode()
            st.rerun()
        return
    pin_input = st.sidebar.text_input("請輸入 PIN 碼以編輯分組", type="password", key="group_edit_pin_input")
    if st.sidebar.button("解鎖編輯", key="unlock_group_editor_btn", width="stretch"):
        if pin_input == GROUP_EDIT_PIN:
            st.session_state.group_editor_unlocked = True
            enter_edit_mode()
            st.sidebar.success("PIN 正確，已解鎖")
            st.rerun()
        else:
            st.sidebar.error("PIN 錯誤")


def render_stock_group_editor():
    st.sidebar.markdown("## 🛠️ 股票分組編輯")
    st.sidebar.checkbox(
        "☁️ 編輯分組時同步提交到 GitHub",
        value=st.session_state.get("sync_groups_to_github", False),
        key="sync_groups_to_github",
        help="需要在 Secrets 設定 GITHUB_TOKEN（且 GITHUB_OWNER/GITHUB_REPO/GITHUB_BRANCH 正確），"
             "否則勾選了也只會存在本機（下次重新部署會遺失）。",
    )
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
        if st.button("新增分類", key="add_group_btn", width="stretch"):
            enter_edit_mode()
            name = new_group_name.strip()
            if not name:
                st.sidebar.warning("請輸入分類名稱")
            elif name in groups:
                st.sidebar.warning("分類名稱已存在")
            else:
                groups[name] = []
                st.session_state.stock_groups = groups
                persist_stock_groups(groups)
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
            quick_input = st.text_input("輸入股票代碼、名稱或 ticker", key="quick_add_symbol_input", on_change=enter_edit_mode)
        resolved_symbol, resolved_name, resolved_type = resolve_stock_query(quick_input)
        if quick_input.strip():
            if resolved_symbol:
                if resolved_name:
                    if resolved_type in ["code", "ticker"]:
                        st.caption(f"查詢結果：{resolved_name} / 將加入：{resolved_symbol}")
                    elif resolved_type in ["name", "name_partial"]:
                        st.caption(f"查詢結果：{resolved_name} → {resolved_symbol}")
                    else:
                        st.caption(f"標準化代碼：{resolved_symbol}")
                else:
                    st.caption(f"標準化代碼：{resolved_symbol}")
            else:
                st.caption(f"查無對應股票，請確認 {STOCK_NAME_FILE} 或輸入完整 ticker")
        with quick_col2:
            if st.button("加入目前分類", key="quick_add_btn", width="stretch"):
                enter_edit_mode()
                symbol, stock_name_for_msg, _ = resolve_stock_query(quick_input)
                if not symbol:
                    st.warning("請輸入股票代碼或股票名稱")
                else:
                    current_list = groups.get(selected_group, [])
                    if symbol in current_list:
                        st.warning("此股票已存在於目前分類")
                    else:
                        current_list.append(symbol)
                        groups[selected_group] = current_list
                        st.session_state.stock_groups = groups
                        persist_stock_groups(groups)
                        set_next_selected_group(selected_group)
                        st.session_state._clear_quick_add_symbol_input = True
                        if stock_name_for_msg:
                            st.session_state._quick_add_success_message = f"已加入 {symbol}（{stock_name_for_msg}）"
                        else:
                            st.session_state._quick_add_success_message = f"已加入 {symbol}"
                        st.rerun()
        col1, col2 = st.columns(2)
        with col1:
            if st.button("💾 儲存分類", key="save_group_btn", width="stretch"):
                new_name = new_group_name.strip()
                if not new_name:
                    st.sidebar.warning("分類名稱不可為空")
                elif new_name != selected_group and new_name in groups:
                    st.sidebar.warning("分類名稱已存在，請使用其他名稱")
                else:
                    updated = {}
                    for k, v in groups.items():
                        updated[new_name if k == selected_group else k] = normalize_symbols_from_text(symbols_text) if k == selected_group else v
                    st.session_state.stock_groups = updated
                    persist_stock_groups(updated)
                    leave_edit_mode()
                    set_next_selected_group(new_name)
                    st.rerun()
        with col2:
            if st.button("🗑️ 刪除分類", key="delete_group_btn", width="stretch"):
                if len(groups) <= 1:
                    st.sidebar.warning("至少保留一個分類")
                else:
                    groups.pop(selected_group, None)
                    st.session_state.stock_groups = groups
                    persist_stock_groups(groups)
                    leave_edit_mode()
                    set_next_selected_group(list(groups.keys())[0])
                    st.rerun()

    with st.sidebar.expander("☁️ GitHub 同步", expanded=False):
        st.caption(f"repo：{github_repo_config()['owner']}/{github_repo_config()['repo']}（分支：{github_repo_config()['branch']}）")
        if st.button("📥 從 GitHub 讀取最新 stock_groups.json", key="pull_groups_from_github_btn", width="stretch"):
            try:
                fetched = fetch_stock_groups_from_github()
                save_backup_snapshot(st.session_state.stock_groups)
                st.session_state.stock_groups = fetched
                save_stock_groups(fetched)  # 本身就是從 GitHub 讀來的，只存本機快取，不用再推回去
                leave_edit_mode()
                set_next_selected_group(list(fetched.keys())[0])
                st.sidebar.success("已從 GitHub 讀取最新股票分組。")
                st.rerun()
            except Exception as e:
                st.sidebar.error(f"從 GitHub 讀取失敗：{e}")
        if st.button("☁️ 手動推送目前分組到 GitHub", key="push_groups_to_github_btn", width="stretch"):
            if upload_stock_groups_to_github(st.session_state.stock_groups):
                st.sidebar.success("已推送到 GitHub。")
            else:
                st.sidebar.warning("推送失敗，請確認 Secrets 中的 GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO 設定。")

    with st.sidebar.expander("📦 備份 / 匯出 / 匯入 JSON", expanded=False):
        export_json_str = json.dumps(st.session_state.stock_groups, ensure_ascii=False, indent=2)
        st.download_button(label="⬇️ 匯出目前分組 JSON", data=export_json_str, file_name="stock_groups.json", mime="application/json", key="download_groups_json_btn", width="stretch")
        if st.button("🗂️ 建立本地備份", key="create_local_backup_btn", width="stretch"):
            try:
                backup_file = save_backup_snapshot(st.session_state.stock_groups)
                st.sidebar.success(f"已建立備份：{os.path.basename(backup_file)}")
            except Exception as e:
                st.sidebar.error(f"建立備份失敗：{e}")
        uploaded_file = st.file_uploader("上傳股票分組 JSON", type=["json"], key="upload_groups_json_file")
        if uploaded_file is not None:
            st.caption("上傳後按下「匯入並覆蓋目前分組」才會生效")
            if st.button("📥 匯入並覆蓋目前分組", key="import_groups_json_btn", width="stretch"):
                try:
                    raw = uploaded_file.read()
                    data = json.loads(raw.decode("utf-8"))
                    validated = validate_and_normalize_group_json(data)
                    save_backup_snapshot(st.session_state.stock_groups)
                    st.session_state.stock_groups = validated
                    persist_stock_groups(validated)
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
        if st.button("還原預設分組", key="reset_groups_btn", width="stretch"):
            try:
                save_backup_snapshot(st.session_state.stock_groups)
            except Exception:
                pass
            st.session_state.stock_groups = copy.deepcopy(DEFAULT_STOCK_GROUPS)
            persist_stock_groups(st.session_state.stock_groups)
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
if os.path.exists(APP_LOGO):
    title_icon_col, title_text_col = st.columns([0.45, 8])
    with title_icon_col:
        st.image(APP_LOGO, width=58)
    with title_text_col:
        st.markdown(
            """
            <h1 style="margin:0; padding-top:4px; font-size:42px; font-weight:800; line-height:1.2;">
                股票監控面板 - 告訴我你會買日月光
            </h1>
            """,
            unsafe_allow_html=True,
        )
else:
    st.markdown(
        """
        <h1 style="margin:0; padding-top:4px; font-size:42px; font-weight:800; line-height:1.2;">
            📊 股票監控面板 - 告訴我你會買日月光
        </h1>
        """,
        unsafe_allow_html=True,
    )

ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns(
    [1.35, 2.25, 1.45, 1.45],
    gap="medium",
    vertical_alignment="center",
)

with ctrl_col1:
    if st.button("🔄 手動更新即時資料", width="stretch"):
        st.cache_data.clear()
        st.rerun()

with ctrl_col2:
    auto_col, label_col, input_col = st.columns(
        [1.05, 0.42, 0.78],
        gap="small",
        vertical_alignment="center",
    )

    with auto_col:
        auto_refresh = st.toggle(
            "⏱️ 啟用自動更新",
            value=st.session_state.auto_refresh_enabled,
            help="開啟後會依照刷新秒數重新整理；WebSocket 即時價會跟著此秒數更新畫面。",
        )

        if auto_refresh != st.session_state.auto_refresh_enabled:
            st.session_state.auto_refresh_enabled = auto_refresh
            st.rerun()

    with label_col:
        st.markdown(
            """
            <div style="
                white-space: nowrap;
                font-size: 14px;
                line-height: 38px;
                margin: 0;
                padding: 0;
                text-align: right;
            ">
                刷新秒數
            </div>
            """,
            unsafe_allow_html=True,
        )

    with input_col:
        st.number_input(
            "刷新秒數",
            min_value=1,
            max_value=300,
            step=1,
            key="refresh_sec",
            label_visibility="collapsed",
            help="自動刷新間隔秒數，預設 3 秒。WebSocket 畫面更新也會依照此秒數。",
        )

with ctrl_col3:
    tg_push = st.toggle(
        "📲 Telegram 推送開關",
        value=st.session_state.tg_push_enabled,
        help="必須開啟此選項，機器人才會發送推播",
    )

    if tg_push != st.session_state.tg_push_enabled:
        st.session_state.tg_push_enabled = tg_push
        st.rerun()

with ctrl_col4:
    sched_push = st.toggle(
        "⏰ 定時推送模式",
        value=st.session_state.scheduled_push_enabled,
        help="開啟後，僅在 09:40, 10:00, 11:00, 12:00, 13:00 執行推播檢查",
    )

    if sched_push != st.session_state.scheduled_push_enabled:
        st.session_state.scheduled_push_enabled = sched_push
        st.rerun()

gc.collect()

with st.sidebar.expander("📊 資料來源設定", expanded=True):
    st.caption("綠色為建議預設值")

    # 第一列：即時資料（當日資料）
    st.markdown("**⏱️ 即時資料（當日資料）**")
    _rt_current = st.session_state.get("realtime_source", "fubon")
    _rt_label = st.radio(
        "即時資料來源",
        options=["富邦 WebSocket", "Yfinance"],
        index=0 if _rt_current == "fubon" else 1,
        horizontal=True,
        key="realtime_source_radio",
        label_visibility="collapsed",
    )
    _rt_new = "fubon" if _rt_label == "富邦 WebSocket" else "yfinance"
    if _rt_new != _rt_current:
        st.session_state.realtime_source = _rt_new
        st.rerun()
    st.caption("13:30 前富邦，13:30 切到 yfinance（選 Yfinance 則全天強制使用 yfinance）")

    st.divider()

    # 第二列：歷史資料（當日以前的資料）
    st.markdown("**📜 歷史資料（當日以前的資料）**")
    _hist_current = st.session_state.get("history_source", "db")
    _hist_label = st.radio(
        "歷史資料來源",
        options=["twse_ohlcv.db", "Yfinance"],
        index=0 if _hist_current == "db" else 1,
        horizontal=True,
        key="history_source_radio",
        label_visibility="collapsed",
    )
    _hist_new = "db" if _hist_label == "twse_ohlcv.db" else "yfinance"
    if _hist_new != _hist_current:
        st.session_state.history_source = _hist_new
        st.rerun()
    if _hist_new == "db" and not os.path.exists(TWSE_DB_PATH):
        st.error(f"找不到資料庫檔案：{TWSE_DB_PATH}")

    st.divider()

    # 第三列：盤後資料（當日+歷史資料）
    st.markdown("**🌙 盤後資料（當日+歷史資料）**")
    _pm_enabled = st.checkbox(
        "啟用盤後資料模式（覆蓋以上兩項設定，當日＋歷史資料合併由單一來源讀取）",
        value=st.session_state.get("post_market_enabled", False),
        key="post_market_enabled_checkbox",
    )
    if _pm_enabled != st.session_state.get("post_market_enabled", False):
        st.session_state.post_market_enabled = _pm_enabled
        st.rerun()
    _pm_current = st.session_state.get("post_market_source", "db")
    _pm_label = st.radio(
        "盤後資料來源",
        options=["twse_ohlcv.db", "Yfinance"],
        index=0 if _pm_current == "db" else 1,
        horizontal=True,
        key="post_market_source_radio",
        label_visibility="collapsed",
        disabled=not _pm_enabled,
    )
    _pm_new = "db" if _pm_label == "twse_ohlcv.db" else "yfinance"
    if _pm_new != _pm_current:
        st.session_state.post_market_source = _pm_new
        st.rerun()
    if _pm_enabled:
        st.caption("⚠️ 已啟用：當日與歷史資料一律合併讀取，不使用富邦 WebSocket。")
        if _pm_new == "db" and not os.path.exists(TWSE_DB_PATH):
            st.error(f"找不到資料庫檔案：{TWSE_DB_PATH}")

render_fubon_login()
render_group_editor_lock()
if st.session_state.group_editor_unlocked:
    render_stock_group_editor()
else:
    st.sidebar.info("目前為唯讀模式：輸入 PIN 後才能修改股票分組")

tw_now = datetime.now(TW_TZ)
st.caption(f"更新時間：{tw_now.strftime('%Y-%m-%d %H:%M:%S')}")

render_taiex_chart()

rise_threshold = st.sidebar.number_input(
    "漲幅門檻 (%)",
    min_value=0.00,
    value=5.00,
    step=1.00,
    format="%.2f",
)

manager = st.session_state.fubon_manager
if st.session_state.fubon_logged_in:
    login_time = st.session_state.get("fubon_login_time")
    can_subscribe = True
    if login_time:
        can_subscribe = (datetime.now(TW_TZ) - login_time).total_seconds() >= 1
    if can_subscribe:
        all_symbols = []
        for stocks in st.session_state.stock_groups.values():
            all_symbols.extend(stocks)
        manager.subscribe_many(all_symbols)
    else:
        st.sidebar.info("等待富邦 WebSocket 連線穩定後訂閱股票...")

with st.sidebar.expander("📡 富邦 WebSocket 狀態", expanded=True):
    status = manager.get_status()
    if status["connected"]:
        st.markdown('<span class="ws-ok">● Connected</span>', unsafe_allow_html=True)
    else:
        st.markdown('<span class="ws-bad">● Not connected</span>', unsafe_allow_html=True)
    st.caption(f"已訂閱：{status['subscribed_count']} 檔")
    if status["last_message_at"]:
        st.caption(f"最後資料：{status['last_message_at'].strftime('%H:%M:%S')}")
    if status["error"]:
        st.warning(status["error"])

with st.sidebar.expander("🕒 目前資料來源狀態", expanded=True):
    if st.session_state.get("post_market_enabled", False):
        _pm_src = st.session_state.get("post_market_source", "db")
        st.info(f"盤後資料模式：當日＋歷史資料皆來自 {'twse_ohlcv.db' if _pm_src == 'db' else 'yfinance'}")
    else:
        _rt_src = st.session_state.get("realtime_source", "fubon")
        _hist_src = st.session_state.get("history_source", "db")
        if _rt_src == "fubon":
            if is_fubon_realtime_time():
                st.info("即時資料：09:00~13:30 優先富邦 WebSocket")
            else:
                st.info("即時資料：13:30 後自動切到 yfinance")
        else:
            st.info("即時資料：強制使用 yfinance")
        st.caption(f"歷史資料來源：{'twse_ohlcv.db' if _hist_src == 'db' else 'yfinance'}")

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
            diff_seconds = (tw_now - target_dt).total_seconds()
            if abs(diff_seconds) <= 45:
                time_str = target_dt.strftime("%H%M")
                today_str = tw_now.strftime("%Y%m%d")
                current_schedule_key = f"slot_{today_str}_{time_str}"
                if current_schedule_key not in st.session_state.processed_time_slots:
                    can_push_now = True
                    break

group_tables = {}
group_up_summary = []
for group_name, stocks in st.session_state.stock_groups.items():
    rows = []
    hit_count = up_count = down_count = flat_count = error_count = 0
    valid_stock_stats = []
    hit_names = []
    for symbol in stocks:
        try:
            raw_df = download_stock_data(symbol)
            df = normalize_ohlc(raw_df)
            if df.empty:
                raise ValueError("無法解析 yfinance 欄位格式")
            price, price_source = get_last_price(symbol, df, manager)
            stock_name = get_stock_name(symbol)
            # 優先使用富邦官方 REST 今日開高低價（100% 準確，交易所自己算好的）；
            # 失敗（未登入、非交易時段、觸發流量限制等）才 fallback 回自己
            # 用 WS 逐筆成交追蹤的 session_low / session_high，確保任何情況下都不會整頁掛掉。
            official_ohlc = get_official_today_ohlc(manager, symbol)
            if official_ohlc.get("low") is not None:
                session_low = official_ohlc["low"]
            else:
                session_low = update_intraday_low(symbol, price, tw_now, price_source)
            if official_ohlc.get("high") is not None:
                session_high = official_ohlc["high"]
            else:
                session_high = update_intraday_high(symbol, price, tw_now, price_source)
            session_open = official_ohlc.get("open") if official_ohlc.get("open") is not None else price

            data = compute_indicators(df, price)
            signal_hits, signal_display = run_stock_signals(
                symbol, stock_name, df,
                open_val=session_open,
                high_val=max(session_high, price),
                low_val=min(session_low, price),
                close_val=price,
                rise_threshold=rise_threshold,
            )

            is_high_gain = data["pct"] >= 5
            has_priority_signal = bool(signal_hits)
            if is_high_gain or has_priority_signal:
                base_symbol = symbol.split('.')[0]
                yahoo_url = f"https://tw.stock.yahoo.com/quote/{base_symbol}"
                symbol_link = f'<a href="{yahoo_url}">{symbol}</a>'
                today_str = tw_now.strftime("%Y-%m-%d")
                notify_key = f"{symbol}_{today_str}"
                if can_push_now and (notify_key not in st.session_state.notified_stocks):
                    msg = (
                        f"🔔 <b>強勢股達標通知：{stock_name} ({symbol_link})</b>\n\n"
                        f"📈 價格：{data['price']}\n"
                        f"🔥 漲幅：{data['pct']:+.2f}%\n"
                        f"📊 買賣訊號：{signal_display}\n"
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
                "買賣訊號": signal_display,
                "價格來源": price_source,
                "_pct_raw": float(data["pct"]),
            })
        except Exception as e:
            error_count += 1
            rows.append({
                "代碼": symbol,
                "代碼網址": "",
                "股票名稱": get_stock_name(symbol),
                "價格": "錯誤",
                "昨收": "-",
                "漲跌%": "-",
                "MA位置": "-",
                "MA排列": "-",
                "K值": "-",
                "D值": "-",
                "買賣訊號": str(e),
                "價格來源": "-",
                "_pct_raw": None,
            })

    hit_names_text = compact_name_list(hit_names, max_show=4)
    top3_html = build_top3_html(valid_stock_stats)
    df_table = pd.DataFrame(rows)
    display_df = df_table.copy()
    if not display_df.empty:
        display_df["漲跌%"] = display_df["漲跌%"].apply(format_color)
        display_df["K值"] = display_df["K值"].apply(format_k)
        display_df["買賣訊號"] = display_df["買賣訊號"].apply(format_signal)
    group_tables[group_name] = {"count": len(stocks), "table": display_df, "raw_rows": rows}
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

# ===== Excel 匯出：把這次掃描結果(全部分組)彙整成一份 Excel 供下載 =====
excel_dl_col1, excel_dl_col2 = st.columns([1.6, 6.4], vertical_alignment="center")
with excel_dl_col1:
    monitor_excel_bytes = build_monitor_excel_bytes(group_tables)
    monitor_excel_filename = f"monitor_snapshot_{tw_now.strftime('%Y%m%d_%H%M%S')}.xlsx"
    st.download_button(
        "📥 下載監控總表 Excel",
        data=monitor_excel_bytes,
        file_name=monitor_excel_filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_monitor_excel_btn",
        width="stretch",
    )
with excel_dl_col2:
    st.caption("Excel 含「全部彙總」分頁 + 各分組各自一個分頁，數值欄位保留原始數字方便排序/篩選。")

render_summary_dashboard(group_up_summary, rise_threshold)
st.divider()
for group_name, info in group_tables.items():
    anchor_id = make_anchor_id(group_name)
    st.markdown(f'<div id="{anchor_id}" style="scroll-margin-top: 80px;"></div>', unsafe_allow_html=True)
    header_col1, header_col2 = st.columns([8, 2])
    with header_col1:
        st.subheader(f"【{group_name}】({info['count']}檔)")
    with header_col2:
        st.markdown(
            '<div style="text-align:right; padding-top:0.4rem;">'
            '<a href="#dashboard-top" class="back-to-dashboard-btn">⬆️ 回到儀表板</a>'
            '</div>',
            unsafe_allow_html=True,
        )
    table_df = info["table"].copy()
    if not table_df.empty and "代碼網址" in table_df.columns:
        table_df["代碼"] = table_df["代碼網址"]
    display_columns = [
        "代碼", "股票名稱", "價格", "昨收", "漲跌%", "MA位置", "MA排列",
        "K值", "D值", "買賣訊號", "價格來源",
    ]

    if table_df.empty:
        st.dataframe(
            table_df[display_columns],
            width="stretch",
            column_config={
                "代碼": st.column_config.LinkColumn(
                    "代碼",
                    help="點擊前往台股 Yahoo 技術分析頁",
                    display_text=r"quote/([^/]+)/technical-analysis",
                ),
                "股票名稱": st.column_config.TextColumn("股票名稱"),
            },
        )
    else:
        # 「股票名稱」欄位底色標示漲停 / 跌停：
        # 紅底＝漲停（漲跌% >= 9.5%），綠底＝跌停（漲跌% <= -9.5%），符合台股慣例（紅漲綠跌）。
        pct_series = table_df["_pct_raw"]
        display_df_view = table_df[display_columns]

        def _highlight_stock_name(row):
            pct = pct_series.get(row.name)
            style = ""
            if pd.notna(pct):
                if pct >= LIMIT_UP_DOWN_PCT_THRESHOLD:
                    style = "background-color: #ff4d4d; color: #ffffff; font-weight: 700;"
                elif pct <= -LIMIT_UP_DOWN_PCT_THRESHOLD:
                    style = "background-color: #2ecc71; color: #ffffff; font-weight: 700;"
            return [style if col == "股票名稱" else "" for col in display_df_view.columns]

        styled_df = display_df_view.style.apply(_highlight_stock_name, axis=1)

        st.dataframe(
            styled_df,
            width="stretch",
            column_config={
                "代碼": st.column_config.LinkColumn(
                    "代碼",
                    help="點擊前往台股 Yahoo 技術分析頁",
                    display_text=r"quote/([^/]+)/technical-analysis",
                ),
                "股票名稱": st.column_config.TextColumn("股票名稱"),
            },
        )
    st.markdown('<div style="margin-bottom: 10px;"></div>', unsafe_allow_html=True)


with st.sidebar.expander("🔍 WebSocket Debug", expanded=False):
    debug_code = st.text_input("輸入代碼看最後 WS 原始訊息", value="4919")
    msg = manager.get_message(debug_code)
    if msg:
        st.caption(f"時間：{msg['time'].strftime('%Y-%m-%d %H:%M:%S')}")
        st.json(msg["raw"])
    else:
        st.caption("尚未收到此代碼的 WebSocket 訊息")

if st.session_state.auto_refresh_enabled and not st.session_state.group_editor_unlocked and not st.session_state.editing_mode:
    refresh_sec = max(1, int(st.session_state.get("refresh_sec", REFRESH_SEC)))
    time.sleep(refresh_sec)
    st.rerun()
