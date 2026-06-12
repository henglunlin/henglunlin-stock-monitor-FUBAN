# -*- coding: utf-8 -*-

import os
import json
import time
import base64
import tempfile
import threading
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

# ===== Fubon API =====
try:
    from fubon_neo.sdk import FubonSDK
except ImportError as e:
    st.error(f"富邦 SDK 載入失敗：{e}")
    st.stop()


# ===== Streamlit config =====
st.set_page_config(layout="wide")


# ===== Constants =====
TW_TZ = ZoneInfo("Asia/Taipei")
DEBUG_SYMBOL = "4919"
REFRESH_SEC = 2


# ===== Safe secrets helper =====
def get_secret_value(path, default=""):
    try:
        obj = st.secrets
        for key in path:
            obj = obj[key]
        return obj
    except Exception:
        return default


# ===== Fubon realtime client =====
class FubonRealtimeDebugClient:
    def __init__(self, fubon_id, fubon_password, cert_password, pfx_base64):
        self.fubon_id = fubon_id
        self.fubon_password = fubon_password
        self.cert_password = cert_password
        self.pfx_base64 = pfx_base64

        self.sdk = None
        self.ws = None

        self.lock = threading.RLock()

        self.logged_in = False
        self.connected = False
        self.subscribed = False
        self.error = None

        self.last_ws_message = None
        self.last_ws_time = None
        self.last_ws_price = None
        self.last_ws_symbol = None
        self.last_ws_channel = None

        self.cert_path = None

    def start(self):
        try:
            self.cert_path = self._write_temp_cert()

            self.sdk = FubonSDK()
            login_result = self.sdk.login(
                self.fubon_id.strip().upper(),
                self.fubon_password,
                self.cert_path,
                self.cert_password,
            )

            # Some SDK versions return result object. Keep it for debug if needed.
            self.login_result = login_result

            self.sdk.init_realtime()
            self.ws = self.sdk.marketdata.websocket_client.stock

            self.ws.on("message", self._on_message)

            self.ws.connect()

            with self.lock:
                self.logged_in = True
                self.connected = True
                self.error = None

            # Give websocket a short moment, then subscribe.
            time.sleep(0.5)
            self.subscribe(DEBUG_SYMBOL)

        except Exception as e:
            with self.lock:
                self.error = str(e)
                self.logged_in = False
                self.connected = False

            raise

    def _write_temp_cert(self):
        cert_bytes = base64.b64decode(self.pfx_base64)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pfx")
        tmp.write(cert_bytes)
        tmp.close()
        return tmp.name

    def _parse_message(self, message):
        if isinstance(message, str):
            try:
                return json.loads(message)
            except Exception:
                return {"raw_text": message}

        if isinstance(message, dict):
            return message

        return {"raw_unknown": str(message)}

    def _extract_price(self, msg):
        """
        Handle possible formats:
        1. {"event":"data","channel":"trades","data":{"symbol":"4919","price":123}}
        2. {"symbol":"4919","price":123}
        3. Other SDK-specific variations.
        """
        data = msg.get("data", {})
        if not isinstance(data, dict):
            data = {}

        symbol = (
            data.get("symbol")
            or msg.get("symbol")
            or data.get("stockNo")
            or msg.get("stockNo")
        )

        channel = (
            msg.get("channel")
            or data.get("channel")
            or msg.get("event")
        )

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

        return symbol, channel, price

    def _on_message(self, message):
        msg = self._parse_message(message)
        symbol, channel, price = self._extract_price(msg)

        now = datetime.now(TW_TZ)

        with self.lock:
            self.last_ws_message = msg
            self.last_ws_time = now
            self.last_ws_symbol = symbol
            self.last_ws_channel = channel

            if symbol and str(symbol) == DEBUG_SYMBOL and price is not None:
                self.last_ws_price = price

    def subscribe(self, symbol):
        if not self.ws:
            return

        code = str(symbol).split(".")[0]

        try:
            self.ws.subscribe({
                "channel": "trades",
                "symbol": code,
            })

            with self.lock:
                self.subscribed = True
                self.error = None

        except Exception as e:
            with self.lock:
                self.error = f"WebSocket subscribe failed: {e}"

    def get_status(self):
        with self.lock:
            return {
                "logged_in": self.logged_in,
                "connected": self.connected,
                "subscribed": self.subscribed,
                "error": self.error,
                "last_ws_price": self.last_ws_price,
                "last_ws_time": self.last_ws_time,
                "last_ws_symbol": self.last_ws_symbol,
                "last_ws_channel": self.last_ws_channel,
                "last_ws_message": self.last_ws_message,
            }

    def get_ws_price(self):
        with self.lock:
            return self.last_ws_price

    def get_sdk(self):
        return self.sdk


@st.cache_resource
def create_fubon_client(fubon_id, fubon_password, cert_password, pfx_base64):
    client = FubonRealtimeDebugClient(
        fubon_id=fubon_id,
        fubon_password=fubon_password,
        cert_password=cert_password,
        pfx_base64=pfx_base64,
    )
    client.start()
    return client


# ===== Data functions =====
@st.cache_data(ttl=60)
def download_daily_candles(_sdk, symbol):
    code = str(symbol).split(".")[0]
    end_date = date.today()
    start_date = end_date - timedelta(days=45)

    res = _sdk.marketdata.rest_client.stock.historical.candles(**{
        "symbol": code,
        "from": start_date.strftime("%Y-%m-%d"),
        "to": end_date.strftime("%Y-%m-%d"),
        "timeframe": "D",
        "fields": "open,high,low,close,volume",
    })

    if not res or "data" not in res:
        return pd.DataFrame()

    df = pd.DataFrame(res["data"])

    if df.empty:
        return pd.DataFrame()

    df = df.rename(columns={
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
        "date": "Date",
    })

    if "Date" in df.columns:
        df = df.sort_values("Date").reset_index(drop=True)

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    required = ["Open", "High", "Low", "Close", "Volume"]
    if not set(required).issubset(df.columns):
        return pd.DataFrame()

    return df[required].dropna(subset=["Close"]).copy()


def get_snapshot_price(_sdk, symbol):
    code = str(symbol).split(".")[0]

    try:
        res = _sdk.marketdata.rest_client.stock.snapshot.quotes(symbol=code)

        if res and "data" in res and len(res["data"]) > 0:
            quote = res["data"][0]

            price_candidates = [
                quote.get("tradePrice"),
                quote.get("lastPrice"),
                quote.get("price"),
                quote.get("close"),
                quote.get("closePrice"),
            ]

            for p in price_candidates:
                if p is not None and pd.notna(p):
                    try:
                        return float(p), quote
                    except Exception:
                        continue

            return None, quote

    except Exception as e:
        return None, {"snapshot_error": str(e)}

    return None, {}


def get_debug_price(client, df):
    """
    Price priority:
    1. WebSocket trades
    2. REST snapshot trade price
    3. Daily candle last close
    """
    sdk = client.get_sdk()

    ws_price = client.get_ws_price()
    if ws_price is not None and pd.notna(ws_price):
        return float(ws_price), "WebSocket trades"

    snapshot_price, quote = get_snapshot_price(sdk, DEBUG_SYMBOL)
    if snapshot_price is not None and pd.notna(snapshot_price):
        return float(snapshot_price), "REST snapshot"

    if df is not None and not df.empty:
        return float(df["Close"].iloc[-1]), "Daily candle fallback"

    raise ValueError("無法取得價格")


def compute_debug_indicators(df, price):
    if df is None or df.empty:
        raise ValueError("日 K 資料為空")

    if len(df) < 20:
        raise ValueError("歷史資料不足，至少需要 20 筆")

    close = pd.to_numeric(df["Close"], errors="coerce")
    high = pd.to_numeric(df["High"], errors="coerce")
    low = pd.to_numeric(df["Low"], errors="coerce")

    yesterday_close = float(close.iloc[-2])
    yesterday_high = float(high.iloc[-2])

    price_val = float(price)
    pct = (price_val / yesterday_close - 1) * 100

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

    k_t = float(k.iloc[-1])
    d_t = float(d.iloc[-1])

    today_low = float(low.iloc[-1])
    gap_signal = "跳空" if today_low > yesterday_high else "-"

    return {
        "price": round(price_val, 2),
        "pct": round(float(pct), 2),
        "ma_range": ma_range,
        "ma_trend": ma_trend,
        "k": round(k_t, 1),
        "d": round(d_t, 1),
        "gap_signal": gap_signal,
        "yesterday_close": yesterday_close,
    }


# ===== UI =====
st.title("📡 富邦 WebSocket 即時價 Debug - 只測 4919")

st.caption(
    "此版本只保留 4919，用來確認富邦 WebSocket trades 是否真的有收到盤中即時成交價。"
)

# Show version to confirm deployed file is updated
st.sidebar.warning("Debug version: 4919 WebSocket trades v1")


# ===== Login panel =====
st.sidebar.markdown("## 🔑 富邦 API 登入")

pfx_base64 = get_secret_value(["fubon", "pfx_base64"], "")

if not pfx_base64:
    st.sidebar.error("找不到 st.secrets['fubon']['pfx_base64']")
    st.stop()

f_id = st.sidebar.text_input("身分證字號", key="debug_fubon_id")
f_pw = st.sidebar.text_input("富邦登入密碼", type="password", key="debug_fubon_pw")
f_cert_pw = st.sidebar.text_input("憑證密碼", type="password", key="debug_fubon_cert_pw")

if st.sidebar.button("清除連線快取 / 重新登入", use_container_width=True):
    create_fubon_client.clear()
    st.rerun()

if not f_id or not f_pw or not f_cert_pw:
    st.warning("請先在左側輸入富邦登入資料與憑證密碼。")
    st.stop()

try:
    client = create_fubon_client(f_id, f_pw, f_cert_pw, pfx_base64)
except Exception as e:
    st.error(f"富邦連線失敗：{e}")
    st.stop()


status = client.get_status()

# ===== Status cards =====
c1, c2, c3, c4 = st.columns(4)

c1.metric("登入", "OK" if status["logged_in"] else "NO")
c2.metric("WebSocket", "Connected" if status["connected"] else "Disconnected")
c3.metric("Subscribed", "YES" if status["subscribed"] else "NO")
c4.metric("Symbol", DEBUG_SYMBOL)

if status["error"]:
    st.error(status["error"])

# ===== Data retrieval =====
sdk = client.get_sdk()

try:
    df = download_daily_candles(sdk, DEBUG_SYMBOL)
except Exception as e:
    st.error(f"日 K 取得失敗：{e}")
    df = pd.DataFrame()

snapshot_price, snapshot_raw = get_snapshot_price(sdk, DEBUG_SYMBOL)

try:
    price, price_source = get_debug_price(client, df)
    indicators = compute_debug_indicators(df, price)
except Exception as e:
    st.error(f"價格或指標計算失敗：{e}")
    price = None
    price_source = "-"
    indicators = {}

# ===== Main debug display =====
st.markdown("## 4919 Debug 結果")

d1, d2, d3, d4 = st.columns(4)

d1.metric("目前使用價格", "-" if price is None else f"{price:.2f}")
d2.metric("價格來源", price_source)
d3.metric("WebSocket 價格", "-" if status["last_ws_price"] is None else f"{status['last_ws_price']:.2f}")
d4.metric("REST Snapshot 價格", "-" if snapshot_price is None else f"{snapshot_price:.2f}")

if status["last_ws_time"]:
    st.caption(f"最後 WebSocket 時間：{status['last_ws_time'].strftime('%Y-%m-%d %H:%M:%S')}")
else:
    st.caption("尚未收到 WebSocket trades 訊息。若非盤中，這是正常現象。")

# ===== Table =====
if indicators:
    table_df = pd.DataFrame([{
        "代碼": DEBUG_SYMBOL,
        "價格": indicators["price"],
        "漲跌%": indicators["pct"],
        "昨收": indicators["yesterday_close"],
        "MA位置": indicators["ma_range"],
        "MA排列": indicators["ma_trend"],
        "K值": indicators["k"],
        "D值": indicators["d"],
        "跳空訊號": indicators["gap_signal"],
        "價格來源": price_source,
    }])

    st.dataframe(table_df, use_container_width=True)

# ===== Raw debug =====
with st.expander("🔍 REST Snapshot 原始資料", expanded=False):
    st.json(snapshot_raw)

with st.expander("🔍 最後 WebSocket 原始訊息", expanded=True):
    st.json(status["last_ws_message"])

with st.expander("🔍 日 K 最後 5 筆", expanded=False):
    st.dataframe(df.tail(5), use_container_width=True)

# ===== Auto refresh =====
auto_refresh = st.toggle("自動刷新 Debug 畫面", value=True)

if auto_refresh:
    time.sleep(REFRESH_SEC)
    st.rerun()
