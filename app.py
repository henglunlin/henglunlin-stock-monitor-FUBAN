import re
import os
import json
import copy
import time
import gc
import requests
import base64
from html import escape
from io import BytesIO
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

# ===== yfinance 選用資料源 =====
try:
    import yfinance as yf
except ImportError:
    yf = None

# ===== 富邦 API 引入 =====
try:
    from fubon_neo.sdk import FubonSDK, Mode
except ImportError:
    st.error("請先安裝富邦 API 套件：執行 `pip install fubon-neo`")
    st.stop()

# ===== Streamlit UI 基本設定（一定要放最前面）=====
st.set_page_config(layout="wide")

# ===== 常數設定 =====
REFRESH_SEC = 3
YFINANCE_HISTORY_CACHE_TTL_SEC = 60 * 60  # yfinance 今日以前歷史資料每小時更新一次
ENABLE_GAP_SIGNAL = True
GROUP_EDIT_PIN = "1219"
GROUPS_FILE = "stock_groups.json"
BACKUP_DIR = "backups"
STOCK_NAME_FILE = "TWstocklistname2.txt"
STOCK_SCAN_FILE = "TWstocklistname2.txt"
FORCE_SCAN_ALL_STOCKS_FROM_FILE = True
ALL_STOCK_GROUP_NAME = "TWstocklistname2 全股票掃描"
AUTO_YFINANCE_AFTER_HOUR = 13
AUTO_YFINANCE_AFTER_MINUTE = 30
APP_LOGO = "dog.jpg"

# ===== Telegram 設定（請替換為你的資訊）=====
TELEGRAM_BOT_TOKEN = st.secrets.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = st.secrets.get("TELEGRAM_CHAT_ID", "")  

DEFAULT_STOCK_GROUPS = {
    "權值股": [
        "2330.TW", "00981A.TW", "2449.TW", "2317.TW", "3711.TW",
        "6488.TWO", "2327.TW", "6176.TW", "2303.TW", "5347.TWO",
    ],
    "自選股1": [
        "3008.TW", "3035.TW", "4566.TW", "4956.TW", "6456.TW",
        "4749.TWO", "6271.TW", "6290.TWO", "4919.TW"
    ],
    "低軌衛星": [
        "6285.TW", "2313.TW",
    ],
    "ABF": [
        "4958.TW", "3037.TW", "8046.TW", "3189.TW",
        "8996.TW", "5439.TWO", "8358.TWO",
    ],
    "記憶體": [
        "6770.TW", "2408.TW", "2344.TW", "8271.TW",
        "4967.TW", "3260.TWO", "2451.TW",
    ],
    "CCL": [
        "2383.TW", "6274.TWO", "6213.TW", "8039.TW"
    ],
    "CPO": [
        "4979.TWO", "3163.TWO", "4977.TW",
        "3081.TWO", "3450.TW", "6442.TW"
    ],
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
</style>
""", unsafe_allow_html=True)

# ===== 分組讀寫 =====
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
    tw_now = datetime.now(ZoneInfo("Asia/Taipei"))
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

# ===== Telegram 工具 =====
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


def send_telegram_document(file_bytes: bytes, filename: str, caption: str = "") -> bool:
    """把 Excel 等檔案傳送到 Telegram。"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        st.error("Telegram Bot Token 或 Chat ID 尚未設定，無法推送檔案。")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption}
    files = {
        "document": (
            filename,
            file_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    try:
        res = requests.post(url, data=data, files=files, timeout=20)
        if res.status_code == 200:
            return True
        st.error(f"Telegram 檔案傳送失敗，API 回傳：{res.text}")
    except Exception as e:
        st.error(f"Telegram 檔案傳送連線失敗: {e}")
    return False

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
    except Exception as e:
        pass
    return False

# ===== Fubon API 行情工具 =====
@st.cache_data(ttl=REFRESH_SEC)
def download_stock_data(symbol: str, _sdk):
    """取得歷史日 K 線資料"""
    if _sdk is None:
        raise ValueError("富邦 API 尚未連線")
        
    fubon_symbol = str(symbol).split(".")[0] # 去除 .TW 後綴
    end_date = date.today()
    start_date = end_date - timedelta(days=90)
    
    try:
        res = _sdk.marketdata.rest_client.stock.historical.candles(**{
            "symbol": fubon_symbol,
            "from": start_date.strftime("%Y-%m-%d"),
            "to": end_date.strftime("%Y-%m-%d"),
            "timeframe": "D",
            "fields": "open,high,low,close,volume"
        })
        
        if res and "data" in res and isinstance(res["data"], list):
            df = pd.DataFrame(res["data"])
            if not df.empty:
                df.rename(columns={
                    "open": "Open",
                    "high": "High",
                    "low": "Low",
                    "close": "Close",
                    "volume": "Volume"
                }, inplace=True)
                
                if "date" in df.columns:
                    df = df.sort_values("date").reset_index(drop=True)
                    
                for col in ["Open", "High", "Low", "Close", "Volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                    
                return df
    except Exception as e:
        print(f"富邦 API 抓取 {fubon_symbol} 歷史 K 線失敗: {e}")
        
    return pd.DataFrame()

def normalize_ohlc(df):
    if df is None or df.empty:
        return pd.DataFrame()
    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    if set(required_cols).issubset(df.columns):
        return df[required_cols].copy()
    return pd.DataFrame()

def get_last_price(symbol, df, _sdk):
    """優先透過 snapshot 取得即時報價，若無則退回 K 線最新收盤價"""
    fubon_symbol = str(symbol).split(".")[0]
    
    if _sdk is not None:
        try:
            res = _sdk.marketdata.rest_client.stock.snapshot.quotes(symbol=fubon_symbol)
            if res and "data" in res and len(res["data"]) > 0:
                quote = res["data"][0]
                price = quote.get("closePrice") or quote.get("tradePrice") or quote.get("close")
                if price is not None and pd.notna(price):
                    return float(price)
        except Exception:
            pass

    if not df.empty and "Close" in df.columns:
        return float(df["Close"].iloc[-1])
        
    raise ValueError("無法取得即時價格")

@st.cache_data(ttl=86400)
def load_stock_name_map(file_path: str = STOCK_NAME_FILE) -> dict:
    name_map = {}
    if not os.path.exists(file_path):
        return name_map
    with open(file_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip().replace("\ufeff", "").replace("\u3000", "")
            if not line: continue
            if "\t" in line:
                parts = [p.strip() for p in line.split("\t") if p.strip()]
                if len(parts) >= 2:
                    name_map[parts[0].upper()] = parts[1].strip()
                    continue
            m = re.match(r"^([^\s]+)\s+(.+)$", line)
            if m:
                name_map[m.group(1).strip().upper()] = m.group(2).strip()
    return name_map


@st.cache_data(ttl=86400)
def load_stock_symbols_from_file(file_path: str = STOCK_SCAN_FILE) -> list:
    """從 TWstocklistname2.txt 讀取所有股票代碼，支援 Tab/空白分隔，並去除重複與異常空白。"""
    symbols = []
    seen = set()
    if not os.path.exists(file_path):
        return symbols
    with open(file_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip().replace("\ufeff", "").replace("\u3000", "")
            if not line:
                continue
            symbol = re.split(r"\s+", line, maxsplit=1)[0].strip().upper()
            if not re.match(r"^[0-9A-Z]+\.(TW|TWO)$", symbol):
                continue
            if symbol not in seen:
                seen.add(symbol)
                symbols.append(symbol)
    return symbols

def load_all_stock_group_from_file() -> dict:
    symbols = load_stock_symbols_from_file(STOCK_SCAN_FILE)
    return {ALL_STOCK_GROUP_NAME: symbols}

def _normalize_yfinance_ohlcv(df):
    """標準化 yfinance 回傳的日 K，保留 Date 方便切分今日 / 今日以前。"""
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.reset_index()
    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    if not set(required_cols).issubset(df.columns):
        return pd.DataFrame()
    date_col = "Date" if "Date" in df.columns else df.columns[0]
    df["Date"] = pd.to_datetime(df[date_col], errors="coerce").dt.date
    for col in required_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[["Date"] + required_cols].dropna(subset=["Date", "Open", "High", "Low", "Close"]).reset_index(drop=True)

@st.cache_data(ttl=YFINANCE_HISTORY_CACHE_TTL_SEC)
def download_stock_data_yfinance_history(symbol: str, today_str: str):
    """yfinance 今日以前歷史日 K：每小時抓一次並存入 Streamlit 快取。"""
    if yf is None:
        return pd.DataFrame()
    try:
        df = yf.download(str(symbol).strip().upper(), period="4mo", interval="1d", auto_adjust=False, progress=False, threads=False)
        df = _normalize_yfinance_ohlcv(df)
        if df.empty:
            return pd.DataFrame()
        today = pd.to_datetime(today_str).date()
        return df[df["Date"] < today].reset_index(drop=True)
    except Exception as e:
        print(f"yfinance 抓取 {symbol} 今日以前歷史 K 線失敗: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=REFRESH_SEC)
def download_stock_data_yfinance_today(symbol: str, today_str: str):
    """yfinance 今日價格 / 今日 K：維持短 TTL，配合畫面刷新秒數更新。"""
    if yf is None:
        return pd.DataFrame()
    try:
        df = yf.download(str(symbol).strip().upper(), period="5d", interval="1d", auto_adjust=False, progress=False, threads=False)
        df = _normalize_yfinance_ohlcv(df)
        if df.empty:
            return pd.DataFrame()
        today = pd.to_datetime(today_str).date()
        return df[df["Date"] >= today].reset_index(drop=True)
    except Exception as e:
        print(f"yfinance 抓取 {symbol} 今日價格失敗: {e}")
        return pd.DataFrame()

def download_stock_data_yfinance(symbol: str):
    """使用 yfinance / Yahoo Finance 取得日 K。

    快取策略：
    - 今日以前的歷史資料：每小時抓一次，避免全市場掃描時頻繁重抓。
    - 今日價格 / 今日 K：依 REFRESH_SEC 短 TTL 更新，保留即時性。
    """
    today_str = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
    history_df = download_stock_data_yfinance_history(symbol, today_str)
    today_df = download_stock_data_yfinance_today(symbol, today_str)

    frames = [df for df in [history_df, today_df] if df is not None and not df.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if "Date" in df.columns:
        df = df.drop_duplicates(subset=["Date"], keep="last").sort_values("Date").reset_index(drop=True)
    return df.reset_index(drop=True)

def resolve_price_source(now_dt=None) -> str:
    mode = st.session_state.get("price_source_mode", "自動")
    if mode in ["WebSocket", "Yfinance"]:
        return mode
    if now_dt is None:
        now_dt = datetime.now(ZoneInfo("Asia/Taipei"))
    cutoff = now_dt.replace(hour=AUTO_YFINANCE_AFTER_HOUR, minute=AUTO_YFINANCE_AFTER_MINUTE, second=0, microsecond=0)
    return "Yfinance" if now_dt >= cutoff else "WebSocket"

def render_price_source_selector(now_dt):
    active_source = resolve_price_source(now_dt)
    source_mode = st.session_state.get("price_source_mode", "自動")
    with st.sidebar.expander("🧭 價格來源模式", expanded=True):
        st.markdown(
            f"""
            <div style="background:#2f4563; color:#35a8ff; border-radius:8px; padding:14px 16px; line-height:1.8; font-weight:600;">
            目前價格模式：{source_mode}；<br>
            13:30 後使用 yfinance；若為昨收則抓 Yahoo TW<br>
            實際使用：{active_source}
            </div>
            """,
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2)
        with c1:
            if st.button("WebSocket", use_container_width=True, key="price_source_ws_btn"):
                st.session_state.price_source_mode = "WebSocket"
                st.cache_data.clear(); st.rerun()
        with c2:
            if st.button("Yfinance", use_container_width=True, key="price_source_yf_btn"):
                st.session_state.price_source_mode = "Yfinance"
                st.cache_data.clear(); st.rerun()
        if st.button("恢復自動模式", use_container_width=True, key="price_source_auto_btn"):
            st.session_state.price_source_mode = "自動"
            st.cache_data.clear(); st.rerun()
    with st.sidebar.expander("🔍 WebSocket Debug", expanded=False):
        st.write(f"price_source_mode = {source_mode}")
        st.write(f"active_source = {active_source}")
        st.write(f"fubon_logged_in = {st.session_state.get('fubon_logged_in', False)}")
        st.write(f"yfinance_installed = {yf is not None}")
    return active_source

def download_stock_data_by_source(symbol: str, _sdk, source: str):
    if source == "Yfinance":
        df = download_stock_data_yfinance(symbol)
        if not df.empty:
            return df
        if _sdk is not None:
            return download_stock_data(symbol, _sdk)
        return pd.DataFrame()
    return download_stock_data(symbol, _sdk)

def get_last_price_by_source(symbol: str, df, _sdk, source: str):
    if source == "Yfinance":
        if df is not None and not df.empty and "Close" in df.columns:
            price = pd.to_numeric(df["Close"], errors="coerce").dropna()
            if not price.empty:
                return float(price.iloc[-1])
        if _sdk is not None:
            return get_last_price(symbol, df, _sdk)
        raise ValueError("yfinance / Yahoo TW 無法取得價格")
    return get_last_price(symbol, df, _sdk)

def normalize_rows_for_excel(rows):
    columns = ["代碼", "股票名稱", "價格", "漲跌%", "成交量(張)", "MA位置", "MA排列", "K值", "D值", "KD訊號", "MACD柱", "MACD訊號", "跳空訊號", "訊號類型", "來源"]
    if not rows:
        return pd.DataFrame(columns=columns)
    df = pd.DataFrame(rows).drop_duplicates(subset=["代碼"]).copy()
    if "代碼網址" in df.columns:
        df.drop(columns=["代碼網址"], inplace=True)
    for col in columns:
        if col not in df.columns:
            df[col] = ""
    return df[columns]


def contains_cjk(text) -> bool:
    """判斷儲存格文字是否包含中文/日文/韓文，用以套用中文字型。"""
    if text is None:
        return False
    s = str(text)
    return any(
        ("\u4e00" <= ch <= "\u9fff") or
        ("\u3400" <= ch <= "\u4dbf") or
        ("\uf900" <= ch <= "\ufaff")
        for ch in s
    )

def apply_excel_fonts(workbook):
    """輸出 Excel 字型：中文使用微軟正黑體，英文/數字使用 Calibri。"""
    from openpyxl.styles import Font

    chinese_font_name = "Microsoft JhengHei"  # 微軟正黑體
    english_font_name = "Calibri"

    for worksheet in workbook.worksheets:
        for row in worksheet.iter_rows():
            for cell in row:
                if cell.value is None:
                    cell.font = Font(name=english_font_name)
                elif contains_cjk(cell.value):
                    cell.font = Font(name=chinese_font_name)
                else:
                    cell.font = Font(name=english_font_name)

def build_signal_excel_bytes(signal_buckets: dict) -> bytes:
    """把 4 種訊號分開輸出成 4 個 Excel 分頁。"""
    gap_rows = signal_buckets.get("跳空", [])
    golden_rows = signal_buckets.get("黃金交叉", [])
    near_golden_rows = signal_buckets.get("即將黃金交叉", [])
    macd_rows = signal_buckets.get("MACD翻正", [])

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        normalize_rows_for_excel(gap_rows).to_excel(writer, sheet_name="跳空", index=False)
        normalize_rows_for_excel(golden_rows).to_excel(writer, sheet_name="黃金交叉", index=False)
        normalize_rows_for_excel(near_golden_rows).to_excel(writer, sheet_name="即將黃金交叉", index=False)
        normalize_rows_for_excel(macd_rows).to_excel(writer, sheet_name="MACD訊號", index=False)
        apply_excel_fonts(writer.book)
    output.seek(0)
    return output.getvalue()

@st.cache_data(ttl=86400)
def get_stock_name(symbol: str, _sdk) -> str:
    name_map = load_stock_name_map(STOCK_NAME_FILE)
    if symbol in name_map:
        return name_map[symbol]
        
    fubon_symbol = str(symbol).split(".")[0]
    if _sdk is not None:
        try:
            res = _sdk.marketdata.rest_client.stock.historical.stats(symbol=fubon_symbol)
            if res and "name" in res:
                return res["name"].strip()
        except Exception:
            pass
            
    return fubon_symbol

# ===== 輔助工具函式 =====
def make_anchor_id(group_name: str) -> str:
    anchor = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", group_name).strip("-")
    return f"group-{anchor}"

def yahoo_quote_url(symbol: str) -> str:
    fubon_symbol = str(symbol).split(".")[0]
    return f"https://tw.stock.yahoo.com/quote/{fubon_symbol}"

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
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result

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
        normalized_symbols = normalize_symbols_from_text(raw_text)
        validated[group_name] = normalized_symbols
    if not validated:
        raise ValueError("JSON 內容為空")
    return validated

def normalize_symbol_quick(input_text: str):
    s = str(input_text).strip().upper()
    if not s:
        return None
    if "." in s:
        return s
    if s.isdigit():
        if s.startswith(("3", "6", "8")):
            return f"{s}.TWO"
        return f"{s}.TW"
    return s

def set_next_selected_group(group_name: str):
    st.session_state._next_selected_group = group_name

def enter_edit_mode():
    st.session_state.editing_mode = True

def leave_edit_mode():
    st.session_state.editing_mode = False

def symbol_to_code(symbol: str) -> str:
    return str(symbol).split(".")[0]

def build_top3_html(valid_stock_stats):
    if not valid_stock_stats:
        return '<span style="color:#666666;">無可用資料</span>'
    top3_sorted = sorted(valid_stock_stats, key=lambda x: x["pct"], reverse=True)[:3]
    parts = []
    for item in top3_sorted:
        pct = float(item["pct"])
        if pct > 0:
            pct_color = "#cf1322"
        elif pct < 0:
            pct_color = "#389e0d"
        else:
            pct_color = "#333333"
        code_text = escape(str(item["code"]))
        name_text = escape(str(item["name"]))
        pct_text = f"{pct:+.1f}%"
        parts.append(
            f'<span style="color:#000000;">{code_text} {name_text} </span>'
            f'<span style="color:{pct_color}; font-weight:600;">{pct_text}</span>'
        )
    return " | ".join(parts)

def compact_name_list(names, max_show=3):
    names = [str(x).strip() for x in names if str(x).strip()]
    if not names:
        return "無"
    if len(names) <= max_show:
        return "、".join(names)
    return "、".join(names[:max_show]) + f" 等{len(names)}檔"

# ===== Session State 初始化 =====
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
    st.session_state.stock_groups = load_all_stock_group_from_file() if FORCE_SCAN_ALL_STOCKS_FROM_FILE else load_stock_groups()

if FORCE_SCAN_ALL_STOCKS_FROM_FILE:
    st.session_state.stock_groups = load_all_stock_group_from_file()
if "price_source_mode" not in st.session_state:
    st.session_state.price_source_mode = "自動"
if "scan_enabled" not in st.session_state:
    st.session_state.scan_enabled = False
if "scan_requested" not in st.session_state:
    st.session_state.scan_requested = False

if "group_editor_unlocked" not in st.session_state:
    st.session_state.group_editor_unlocked = False

if "editing_mode" not in st.session_state:
    st.session_state.editing_mode = False

if "fubon_sdk" not in st.session_state:
    st.session_state.fubon_sdk = None

if "fubon_logged_in" not in st.session_state:
    st.session_state.fubon_logged_in = False

if "selected_group_editor" not in st.session_state:
    group_names_init = list(st.session_state.stock_groups.keys())
    st.session_state.selected_group_editor = group_names_init[0] if group_names_init else ""

if "rename_group_input" not in st.session_state:
    st.session_state.rename_group_input = st.session_state.selected_group_editor

if "symbols_text_area" not in st.session_state:
    selected = st.session_state.selected_group_editor
    st.session_state.symbols_text_area = "\n".join(
        st.session_state.stock_groups.get(selected, [])
    )

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
        st.session_state.symbols_text_area = "\n".join(
            st.session_state.stock_groups.get(pending_group, [])
        )

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

# ===== UI 元件 =====
def render_auto_refresh_settings():
    with st.sidebar.expander("🔄 自動刷新設定", expanded=True):
        st.toggle(
            "啟用自動刷新",
            key="auto_refresh_enabled",
            help="開啟後會依照下方秒數自動重新整理；分組編輯解鎖或編輯中會自動暫停。",
        )
        st.number_input(
            "刷新秒數",
            min_value=1,
            max_value=300,
            step=1,
            key="refresh_sec",
            help="自動刷新間隔秒數，預設 3 秒。",
        )

def render_fubon_login():
    st.sidebar.markdown("## 🔑 富邦 API 設定 (Fubon Neo)")
    
    # 已經登入成功就顯示狀態與登出按鈕
    if st.session_state.fubon_logged_in:
        st.sidebar.success("✅ 富邦 API 已成功連線")
        if st.sidebar.button("登出 / 重新連線", use_container_width=True):
            st.session_state.fubon_sdk = None
            st.session_state.fubon_logged_in = False
            st.rerun()
        return

    # 嘗試從 Secrets 讀取憑證檔案 (現在只需要讀取 Base64 字串)
    try:
        fubon_secrets = st.secrets["fubon"]
        pfx_base64 = fubon_secrets["pfx_base64"]
    except KeyError:
        st.sidebar.error("❌ 找不到 Streamlit Secrets 中的 pfx_base64 憑證資料。")
        return

    # 在側邊欄顯示輸入框，讓使用者每次手動輸入完整登入資訊
    st.sidebar.info("請輸入富邦證券登入資訊")
    f_id = st.sidebar.text_input("身分證字號", key="f_id_input")
    f_pw = st.sidebar.text_input("富邦登入密碼", key="f_pw_input", type="password")
    f_cert_pw = st.sidebar.text_input("憑證密碼", key="f_cert_pw_input", type="password")

    if st.sidebar.button("連線行情伺服器", use_container_width=True):
        if not f_id or not f_pw or not f_cert_pw:
            st.sidebar.warning("請填寫完整的身分證字號與密碼！")
        else:
            try:
                # 1. 將 Base64 文字還原為暫存的 .pfx 檔案
                temp_cert_path = "temp_cloud_cert.pfx"
                with open(temp_cert_path, "wb") as f:
                    f.write(base64.b64decode(pfx_base64))
                    
                # 2. 執行登入 (合併使用者輸入的帳密與雲端的檔案)
                with st.spinner("連線富邦 API 中..."):
                    sdk = FubonSDK()
                    # 確保傳入的身分證字號英文是大寫 (.upper())
                    sdk.login(f_id.strip().upper(), f_pw, temp_cert_path, f_cert_pw)
                    sdk.init_realtime()
                    st.session_state.fubon_sdk = sdk
                    st.session_state.fubon_logged_in = True
                    
                st.sidebar.success("✅ 富邦 API 連線成功！")
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

    pin_input = st.sidebar.text_input(
        "請輸入 PIN 碼以編輯分組", type="password", key="group_edit_pin_input"
    )
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
                        if k == selected_group:
                            updated[new_name] = new_symbols
                        else:
                            updated[k] = v
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
                    remaining = list(groups.keys())
                    set_next_selected_group(remaining[0])
                    st.rerun()

    with st.sidebar.expander("📦 備份 / 匯出 / 匯入 JSON", expanded=False):
        export_json_str = json.dumps(st.session_state.stock_groups, ensure_ascii=False, indent=2)
        st.download_button(label="⬇️ 匯出目前分組 JSON", data=export_json_str, file_name="stock_groups.json", mime="application/json", key="download_groups_json_btn", use_container_width=True)
        if st.button("🗂️ 建立本地備份", key="create_local_backup_btn", use_container_width=True):
            try:
                backup_file = save_backup_snapshot(st.session_state.stock_groups)
                st.sidebar.success(f"已建立備份：{os.path.basename(backup_file)}")
            except Exception as e:
                st.sidebar.error(f"建立備份失敗：{e}")
        uploaded_file = st.file_uploader("上傳股票分組 JSON", type=["json"], key="upload_groups_json_file")
        if uploaded_file is not None:
            st.caption("上傳後按下「匯入並覆蓋目前分組」才會生效")
            if st.button("📥 匯入並覆蓋目前分組", key="import_groups_json_btn", use_container_width=True):
                try:
                    raw = uploaded_file.read()
                    data = json.loads(raw.decode("utf-8"))
                    validated = validate_and_normalize_group_json(data)
                    save_backup_snapshot(st.session_state.stock_groups)
                    st.session_state.stock_groups = validated
                    save_stock_groups(validated)
                    leave_edit_mode()
                    first_group = list(validated.keys())[0]
                    set_next_selected_group(first_group)
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
            first_group = list(st.session_state.stock_groups.keys())[0]
            set_next_selected_group(first_group)
            st.rerun()

    with st.sidebar.expander("👀 分組預覽", expanded=False):
        for g, symbols in st.session_state.stock_groups.items():
            st.markdown(f"**{g}**（{len(symbols)}檔）")
            st.caption(", ".join(symbols) if symbols else "（空）")

def compute_indicators(df, price):
    if df is None or df.empty:
        raise ValueError("下載資料為空")
    if len(df) < 20:
        raise ValueError("歷史資料不足（至少需要 20 筆）")

    close = pd.to_numeric(df["Close"].squeeze(), errors="coerce")
    low = pd.to_numeric(df["Low"].squeeze(), errors="coerce")
    high = pd.to_numeric(df["High"].squeeze(), errors="coerce")
    volume = pd.to_numeric(df["Volume"].squeeze(), errors="coerce") if "Volume" in df.columns else pd.Series(dtype="float64")
    if close.isna().all() or low.isna().all() or high.isna().all():
        raise ValueError("OHLC 資料格式異常")

    yesterday_close = float(close.iloc[-2])
    if pd.isna(yesterday_close) or yesterday_close == 0:
        raise ValueError("昨收資料異常")

    price_val = float(price)
    change_pct = float((price_val / yesterday_close - 1) * 100)
    ma5 = float(close.tail(5).mean())
    ma10 = float(close.tail(10).mean())
    ma20 = float(close.tail(20).mean())

    if price_val > ma5: ma_range = ">MA5"
    elif ma5 >= price_val > ma10: ma_range = "MA5~10"
    elif ma10 >= price_val > ma20: ma_range = "MA10~20"
    else: ma_range = "<MA20"

    if ma5 > ma10 > ma20: ma_trend = "多頭"
    elif ma5 < ma10 < ma20: ma_trend = "空頭"
    else: ma_trend = "糾結"

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
    k_y = float(k.iloc[-2])
    d_y = float(d.iloc[-2])

    if k_y <= d_y and k_t > d_t: kd_signal = "黃金交叉"
    elif k_y >= d_y and k_t < d_t: kd_signal = "死亡交叉"
    elif k_t < d_t and (d_t - k_t) < 3: kd_signal = "即將黃金交叉"
    elif k_t > d_t and (k_t - d_t) < 3: kd_signal = "即將死亡交叉"
    elif k_t < 25: kd_signal = "超賣"
    else: kd_signal = "-"

    # ===== MACD 計算 =====
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd_hist = dif - dea
    if len(macd_hist.dropna()) < 2:
        raise ValueError("MACD 計算資料不足")
    macd_hist_t = float(macd_hist.iloc[-1])
    macd_hist_y = float(macd_hist.iloc[-2])
    if macd_hist_y <= 0 and macd_hist_t > 0:
        macd_signal = "MACD翻正"
    elif macd_hist_y >= 0 and macd_hist_t < 0:
        macd_signal = "MACD翻負"
    else:
        macd_signal = "-"

    latest_volume = 0.0
    if not volume.empty and pd.notna(volume.iloc[-1]):
        latest_volume = float(volume.iloc[-1])
    volume_lots = latest_volume / 1000

    gap_signal = "-"
    today_low = float(low.iloc[-1])
    yesterday_high = float(high.iloc[-2])
    if ENABLE_GAP_SIGNAL and pd.notna(today_low) and pd.notna(yesterday_high) and today_low > yesterday_high:
        gap_signal = "跳空"

    return {
        "price": round(price_val, 2),
        "pct": round(change_pct, 2),
        "ma_range": ma_range,
        "ma_trend": ma_trend,
        "k": round(k_t, 1),
        "d": round(d_t, 1),
        "kd_signal": kd_signal,
        "gap_signal": gap_signal,
        "macd_hist": round(macd_hist_t, 4),
        "macd_signal": macd_signal,
        "volume": int(latest_volume),
        "volume_lots": round(volume_lots, 1),
    }

def format_color(val):
    if isinstance(val, (int, float)):
        if val > 0: return f"🔴 +{val:.2f}%"
        elif val < 0: return f"🟢 {val:.2f}%"
        else: return f"{val:.2f}%"
    return val

def format_k(val):
    if isinstance(val, (int, float)):
        if val >= 74: return f"🔴 {val:.1f}"
        elif val >= 50: return f"🟡 {val:.1f}"
        else: return f"🟢 {val:.1f}"
    return val

def format_gap(val):
    if val == "跳空": return "🔴 跳空"
    return "-"

def format_volume(val):
    try:
        return f"{float(val):,.1f}"
    except Exception:
        return val


def render_scan_progress_card(placeholder, pct: float, status_text: str = "掃描進度"):
    """右上角掃描進度卡片：以百分比顯示，進度條仍另外保留。"""
    pct = max(0.0, min(float(pct), 100.0))
    placeholder.markdown(
        f"""
        <div style="
            width: 120px;
            min-height: 78px;
            border: none;
            border-radius: 0;
            padding: 8px 10px;
            text-align: left;
            background: transparent;
            box-sizing: border-box;
        ">
            <div style="font-size: 30px; line-height: 1; font-weight: 800;">{pct:.0f}%</div>
            <div style="font-size: 13px; margin-top: 8px;">{status_text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

def render_summary_dashboard(group_up_summary, rise_threshold):
    st.markdown("### 📌 漲幅儀表板")
    st.caption(f"目前儀表板統計門檻：漲幅 ≥ {rise_threshold}%")
    html_parts = []
    html_parts.append('<div class="dashboard-scroll"><div class="dashboard-grid">')

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
        if hit_ratio >= 60: bg_color = "#fff1f0"; border_color = "#ff7875"; accent_color = "#cf1322"
        elif hit_ratio > 0: bg_color = "#fff7e6"; border_color = "#ffa940"; accent_color = "#d46b08"
        else: bg_color = "#f6ffed"; border_color = "#95de64"; accent_color = "#389e0d"

        card_html = (
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
        html_parts.append(card_html)
    html_parts.append("</div></div>")
    st.markdown("".join(html_parts), unsafe_allow_html=True)

# ==================== 主畫面開始 ====================
# 建立：Logo 欄 / 標題欄 / 右上掃描進度欄，避免 scan_progress_col 未定義。
st.markdown('<div id="dashboard-top" style="scroll-margin-top: 90px;"></div>', unsafe_allow_html=True)

title_icon_col, title_text_col, scan_progress_col = st.columns([0.45, 7.55, 1])

with title_icon_col:
    if os.path.exists(APP_LOGO):
        st.image(APP_LOGO, width=58)
    else:
        st.markdown('<div style="font-size:42px; line-height:1.2;">📊</div>', unsafe_allow_html=True)

with title_text_col:
    st.markdown(
        """
        <h1 style="margin:0; padding-top:4px; font-size:42px; font-weight:800; line-height:1.2;">
            台股掃描器 - 告訴我你會買日月光
        </h1>
        """,
        unsafe_allow_html=True,
    )

with scan_progress_col:
    scan_progress_card_placeholder = st.empty()

render_scan_progress_card(scan_progress_card_placeholder, 0, "掃描進度")
st.markdown('<div style="margin-bottom: 10px;"></div>', unsafe_allow_html=True)

gc.collect()

render_fubon_login()

tw_now = datetime.now(ZoneInfo("Asia/Taipei"))
active_price_source = render_price_source_selector(tw_now)
render_auto_refresh_settings()

if FORCE_SCAN_ALL_STOCKS_FROM_FILE:
    all_symbols_count = len(st.session_state.stock_groups.get(ALL_STOCK_GROUP_NAME, []))
    st.sidebar.success(f"✅ 全市場掃描模式：已從 {STOCK_SCAN_FILE} 載入 {all_symbols_count} 檔股票")
    st.sidebar.caption("此模式會忽略 stock_groups.json 與手動分組，直接掃描 txt 內全部股票。")
else:
    render_group_editor_lock()
    if st.session_state.group_editor_unlocked:
        render_stock_group_editor()
    else:
        st.sidebar.info("目前為唯讀模式：輸入 PIN 後才能修改股票分組")

st.caption(f"更新時間：{tw_now.strftime('%Y-%m-%d %H:%M:%S')}｜價格來源：{active_price_source}")

rise_threshold = st.slider("儀表板漲幅達標門檻 (%)", min_value=5, max_value=9, value=5, step=1)

st.markdown("### 🎯 掃描條件")
scan_btn_col1, scan_btn_col2, scan_col1, scan_col2, scan_col3, scan_macd_col, scan_vol_col, scan_col4 = st.columns([0.9, 0.9, 1.3, 0.9, 1.4, 1.1, 1.2, 1.8])
with scan_btn_col1:
    if st.button("▶️ 開始掃描", use_container_width=True, disabled=st.session_state.scan_enabled):
        st.session_state.scan_enabled = True
        st.session_state.scan_requested = True
        st.cache_data.clear()
        st.rerun()
with scan_btn_col2:
    if st.button("⏹️ 停止掃描", use_container_width=True, disabled=not st.session_state.scan_enabled):
        st.session_state.scan_enabled = False
        st.session_state.scan_requested = False
        st.rerun()
with scan_col1:
    show_only_signal_rows = st.toggle("只顯示訊號股票", value=True, help="開啟後，主表只列出：跳空、黃金交叉、即將黃金交叉、MACD翻正")
with scan_col2:
    include_gap_signal_filter = st.checkbox("跳空", value=True)
with scan_col3:
    include_kd_signal_filter = st.checkbox("黃金交叉 / 即將黃金交叉", value=True)
with scan_macd_col:
    include_macd_signal_filter = st.checkbox("MACD翻正", value=True)
with scan_vol_col:
    min_volume_lots = st.number_input(
        "成交量(張)下限",
        min_value=0,
        value=1000,
        step=100,
        help="只保留成交量(張) >= 此數值的掃描結果；預設 1000 張。"
    )
with scan_col4:
    scan_action_placeholder = st.empty()

if st.session_state.scan_enabled:
    st.caption("🟢 掃描狀態：執行中")
elif "last_scan_result" in st.session_state:
    st.caption(
        f"✅ 掃描狀態：已完成，上次完成時間：{st.session_state.last_scan_result.get('scan_completed_at', '-')}｜成交量下限：{st.session_state.last_scan_result.get('min_volume_lots', 1000)} 張"
    )
else:
    st.caption("⚪ 掃描狀態：已停止，按「開始掃描」才會抓取資料。")

selected_signal_names = []
if include_gap_signal_filter:
    selected_signal_names.append("跳空")
if include_kd_signal_filter:
    selected_signal_names.extend(["黃金交叉", "即將黃金交叉"])
if include_macd_signal_filter:
    selected_signal_names.append("MACD翻正")
if not selected_signal_names:
    st.warning("請至少勾選一種掃描訊號，否則不會列出訊號股票。")

# 依價格來源檢查必要套件與登入狀態
if active_price_source == "WebSocket" and not st.session_state.fubon_logged_in:
    st.warning("⚠️ 目前價格來源為 WebSocket，請先至左側面板連線「富邦 API」，才能開始抓取行情資料。")
    st.stop()
if active_price_source == "Yfinance" and yf is None:
    st.warning("⚠️ 目前價格來源為 Yfinance，請先安裝套件：pip install yfinance")
    st.stop()

should_run_scan = bool(st.session_state.pop("scan_requested", False))
has_last_scan_result = "last_scan_result" in st.session_state

if not should_run_scan and not has_last_scan_result:
    render_scan_progress_card(scan_progress_card_placeholder, 0, "掃描進度")
    st.info("請按「開始掃描」開始抓取股票資料。")
    st.stop()

if should_run_scan:
    # ===== 推送時間與手動指令邏輯判斷 =====
    can_push_now = False
    current_schedule_key = None
    manual_push_triggered = False

    if st.session_state.tg_push_enabled:
        # 偷偷去問 Telegram 有沒有收到 push 指令
        manual_push_triggered = check_telegram_push_command()
    
        if manual_push_triggered:
            can_push_now = True
            st.session_state.notified_stocks = set() # 清空今日已通知紀錄，強制重發
            st.toast("🚀 收到 'push' 指令，強制觸發推播！")
            send_telegram_message("🤖 <b>收到指令，開始為您掃描並強制推播強勢股...</b>")
        elif st.session_state.scheduled_push_enabled:
            # 定義每天的目標發送時間
            TARGET_TIMES = [
                tw_now.replace(hour=9, minute=40, second=0, microsecond=0),
                tw_now.replace(hour=10, minute=0, second=0, microsecond=0),
                tw_now.replace(hour=11, minute=0, second=0, microsecond=0),
                tw_now.replace(hour=12, minute=0, second=0, microsecond=0),
                tw_now.replace(hour=13, minute=0, second=0, microsecond=0)
            ]

            for target_dt in TARGET_TIMES:
                # 計算當下時間與目標時間的差距（秒）
                diff_seconds = (tw_now - target_dt).total_seconds()
            
                # 若時間差距在正負 60 秒以內
                if abs(diff_seconds) <= 45:
                    # 產生唯一的排程 Key，例如 slot_20260609_0940
                    time_str = target_dt.strftime("%H%M")
                    today_str = tw_now.strftime("%Y%m%d")
                    current_schedule_key = f"slot_{today_str}_{time_str}"
                
                    # 檢查該時段今天是否已經觸發過
                    if current_schedule_key not in st.session_state.processed_time_slots:
                        can_push_now = True
                        break  # 條件符合就跳出迴圈
        else:
            # 修正：關閉排程時不應預設推播，否則 Streamlit 重刷就會一直送訊息
            can_push_now = False

    group_tables = {}
    group_up_summary = []
    all_signal_rows = []
    signal_buckets = {"跳空": [], "黃金交叉": [], "即將黃金交叉": [], "MACD翻正": []}
    scan_total_count = sum(len(stocks) for stocks in st.session_state.stock_groups.values())
    render_scan_progress_card(scan_progress_card_placeholder, 0, "掃描進度")
    progress_bar = st.progress(0, text=f"掃描進度：0.0%（準備掃描 {scan_total_count} 檔股票）")
    processed_count = 0

    for group_name, stocks in st.session_state.stock_groups.items():
        rows = []
        hit_count = up_count = down_count = flat_count = error_count = 0
        valid_stock_stats = []
        hit_names = []

        for symbol in stocks:
            if not st.session_state.scan_enabled:
                progress_bar.empty()
                st.warning("掃描已停止。")
                st.stop()
            processed_count += 1
            if scan_total_count > 0:
                progress_value = min(processed_count / scan_total_count, 1.0)
                progress_pct = progress_value * 100
                render_scan_progress_card(scan_progress_card_placeholder, progress_pct, "掃描進度")
                progress_bar.progress(progress_value, text=f"掃描進度：{progress_pct:.1f}%（{processed_count}/{scan_total_count}：{symbol}）")
            try:
                df = download_stock_data_by_source(symbol, st.session_state.fubon_sdk, active_price_source)
                df = normalize_ohlc(df)
                if df.empty: raise ValueError("無效的 K 線資料")

                price = get_last_price_by_source(symbol, df, st.session_state.fubon_sdk, active_price_source)
                stock_name = get_stock_name(symbol, st.session_state.fubon_sdk)
                data = compute_indicators(df, price)

                signal_types = []
                if data["gap_signal"] == "跳空":
                    signal_types.append("跳空")
                if data["kd_signal"] in ["黃金交叉", "即將黃金交叉"]:
                    signal_types.append(data["kd_signal"])
                if data["macd_signal"] == "MACD翻正":
                    signal_types.append("MACD翻正")
                passes_volume_filter = float(data.get("volume_lots", 0)) >= float(min_volume_lots)
                is_selected_signal = any(sig in selected_signal_names for sig in signal_types) and passes_volume_filter

                # ===== 執行推播檢查 =====
                is_high_gain = data["pct"] >= 5
                if (is_high_gain or is_selected_signal) and passes_volume_filter:
                    base_symbol = symbol.split('.')[0]
                    yahoo_url = f"https://tw.stock.yahoo.com/quote/{base_symbol}"
                    symbol_link = f'<a href="{yahoo_url}">{symbol}</a>'
                    today_str = tw_now.strftime("%Y-%m-%d")
                    notify_key = f"{symbol}_{today_str}"
                    if can_push_now and (notify_key not in st.session_state.notified_stocks):
                        msg = (
                            f"🔔 <b>全市場掃描訊號：{stock_name} ({symbol_link})</b>\n\n"
                            f"📈 價格：{data['price']}\n"
                            f"🔥 漲幅：{data['pct']}%\n"
                            f"📦 成交量：{data['volume_lots']:,.1f} 張\n"
                            f"📊 KD訊號：{data['kd_signal']}\n"
                            f"🧭 MACD訊號：{data['macd_signal']} / MACD柱：{data['macd_hist']}\n"
                            f"🚀 跳空訊號：{data['gap_signal']}\n"
                            f"🔌 來源：{active_price_source}"
                        )
                        send_telegram_message(msg)
                        st.session_state.notified_stocks.add(notify_key)
                # =======================

                if data["pct"] >= rise_threshold:
                    hit_count += 1
                    hit_names.append(stock_name)
                if data["pct"] > 0: up_count += 1
                elif data["pct"] < 0: down_count += 1
                else: flat_count += 1

                valid_stock_stats.append({"symbol": symbol, "code": symbol_to_code(symbol), "name": stock_name, "pct": float(data["pct"])})
                row = {
                    "代碼": symbol, "代碼網址": yahoo_quote_url(symbol), "股票名稱": stock_name,
                    "價格": f"{data['price']:.2f}", "漲跌%": data["pct"],
                    "成交量(張)": data["volume_lots"],
                    "MA位置": data["ma_range"], "MA排列": data["ma_trend"],
                    "K值": data["k"], "D值": f"{data['d']:.1f}",
                    "KD訊號": data["kd_signal"], "MACD柱": data["macd_hist"],
                    "MACD訊號": data["macd_signal"], "跳空訊號": data["gap_signal"],
                    "訊號類型": "、".join(signal_types) if signal_types else "-",
                    "來源": active_price_source,
                }
                if ((not show_only_signal_rows) or is_selected_signal) and passes_volume_filter:
                    rows.append(row)
                if is_selected_signal:
                    all_signal_rows.append(row.copy())
                    for sig in signal_types:
                        if sig in signal_buckets and sig in selected_signal_names:
                            signal_buckets[sig].append(row.copy())
            except Exception as e:
                error_count += 1
                if not show_only_signal_rows:
                    rows.append({
                        "代碼": symbol, "代碼網址": "", "股票名稱": get_stock_name(symbol, st.session_state.fubon_sdk),
                        "價格": "錯誤", "漲跌%": "-", "成交量(張)": "-",
                        "MA位置": "-", "MA排列": "-", "K值": "-", "D值": "-",
                        "KD訊號": "-", "MACD柱": "-", "MACD訊號": "-",
                        "跳空訊號": str(e), "訊號類型": "錯誤", "來源": active_price_source,
                    })

        hit_names_text = compact_name_list(hit_names, max_show=4)
        top3_html = build_top3_html(valid_stock_stats)
        df_table = pd.DataFrame(rows)
        display_df = df_table.copy()
        if not display_df.empty:
            display_df["漲跌%"] = display_df["漲跌%"].apply(format_color)
            display_df["K值"] = display_df["K值"].apply(format_k)
            display_df["成交量(張)"] = display_df["成交量(張)"].apply(format_volume)
            display_df["跳空訊號"] = display_df["跳空訊號"].apply(format_gap)
        group_tables[group_name] = {"count": len(stocks), "table": display_df}
        group_up_summary.append({
            "分類": group_name, "達標數": hit_count, "達標股票名稱": hit_names_text,
            "前三名HTML": top3_html, "上漲數": up_count, "下跌數": down_count,
            "平盤數": flat_count, "錯誤數": error_count, "總數": len(stocks)
        })

    render_scan_progress_card(scan_progress_card_placeholder, 100, "掃描進度")
    progress_bar.empty()
    if can_push_now and st.session_state.scheduled_push_enabled and current_schedule_key and not manual_push_triggered:
        st.session_state.processed_time_slots.add(current_schedule_key)


    # 掃描完成後將結果保存在 session_state；下載或 Telegram 推送造成 rerun 時，不會重新進入掃描。
    st.session_state.last_scan_result = {
        "group_tables": group_tables,
        "group_up_summary": group_up_summary,
        "all_signal_rows": all_signal_rows,
        "signal_buckets": signal_buckets,
        "excel_filename": f"TWstock_signal_scan_{tw_now.strftime('%Y%m%d_%H%M%S')}.xlsx",
        "scan_completed_at": tw_now.strftime('%Y-%m-%d %H:%M:%S'),
        "progress_pct": 100,
        "min_volume_lots": min_volume_lots,
    }
    st.session_state.scan_enabled = False
else:
    last_scan_result = st.session_state.last_scan_result
    group_tables = last_scan_result.get("group_tables", {})
    group_up_summary = last_scan_result.get("group_up_summary", [])
    all_signal_rows = last_scan_result.get("all_signal_rows", [])
    signal_buckets = last_scan_result.get("signal_buckets", {"跳空": [], "黃金交叉": [], "即將黃金交叉": [], "MACD翻正": []})
    render_scan_progress_card(scan_progress_card_placeholder, last_scan_result.get("progress_pct", 100), "掃描進度")
excel_bytes = build_signal_excel_bytes(signal_buckets)
excel_filename = st.session_state.get("last_scan_result", {}).get(
    "excel_filename",
    f"TWstock_signal_scan_{tw_now.strftime('%Y%m%d_%H%M%S')}.xlsx"
)

with scan_action_placeholder.container():
    bcol1, bcol2 = st.columns(2)
    with bcol1:
        st.download_button("下載", data=excel_bytes, file_name=excel_filename, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True, key="download_signal_excel_btn")
    with bcol2:
        if st.button("推送到telegram", use_container_width=True, key="push_signal_excel_to_tg_btn"):
            ok = send_telegram_document(excel_bytes, excel_filename, caption=f"TWstock 訊號掃描結果：跳空 / 黃金交叉 / 即將黃金交叉 / MACD訊號｜成交量下限 {st.session_state.get('last_scan_result', {}).get('min_volume_lots', min_volume_lots)} 張｜{tw_now.strftime('%Y-%m-%d %H:%M:%S')}")
            if ok:
                st.success("已將 Excel 推送到 Telegram。")

st.markdown("### 🔎 訊號掃描結果")
unique_signal_count = len(pd.DataFrame(all_signal_rows).drop_duplicates(subset=["代碼"])) if all_signal_rows else 0
st.metric("符合勾選訊號股票數", unique_signal_count)
if all_signal_rows:
    signal_df = pd.DataFrame(all_signal_rows).drop_duplicates(subset=["代碼"])
    signal_display_df = signal_df.copy()
    signal_display_df["漲跌%"] = signal_display_df["漲跌%"].apply(format_color)
    signal_display_df["K值"] = signal_display_df["K值"].apply(format_k)
    signal_display_df["成交量(張)"] = signal_display_df["成交量(張)"].apply(format_volume)
    signal_display_df["跳空訊號"] = signal_display_df["跳空訊號"].apply(format_gap)
    signal_display_df["代碼"] = signal_display_df["代碼網址"]
    for col in ["MA位置", "MA排列"]:
        if col not in signal_display_df.columns:
            signal_display_df[col] = "-"
    signal_columns = ["代碼", "股票名稱", "價格", "漲跌%", "成交量(張)", "MA位置", "MA排列", "K值", "D值", "KD訊號", "MACD柱", "MACD訊號", "跳空訊號", "訊號類型", "來源"]
    st.dataframe(signal_display_df[signal_columns], use_container_width=True, column_config={
        "代碼": st.column_config.LinkColumn("代碼", help="點擊前往台股 Yahoo", display_text=r"https://tw.stock.yahoo.com/quote/(.*)"),
        "股票名稱": st.column_config.TextColumn("股票名稱")
    })
    st.markdown("### 📑 依訊號分頁查看")

    signal_tab_specs = [
        ("跳空", "跳空"),
        ("黃金交叉", "黃金交叉"),
        ("即將黃金交叉", "即將黃金交叉"),
        ("MACD 訊號", "MACD翻正"),
    ]

    tab_labels = []
    for display_name, bucket_key in signal_tab_specs:
        bucket_rows = signal_buckets.get(bucket_key, [])
        unique_count = len(pd.DataFrame(bucket_rows).drop_duplicates(subset=["代碼"])) if bucket_rows else 0
        tab_labels.append(f"{display_name}（{unique_count}）")

    signal_tabs = st.tabs(tab_labels)
    for tab, (display_name, bucket_key) in zip(signal_tabs, signal_tab_specs):
        with tab:
            bucket_rows = signal_buckets.get(bucket_key, [])
            unique_count = len(pd.DataFrame(bucket_rows).drop_duplicates(subset=["代碼"])) if bucket_rows else 0
            st.markdown(f"#### {display_name}（{unique_count} 檔）")

            if bucket_rows:
                bucket_df = pd.DataFrame(bucket_rows).drop_duplicates(subset=["代碼"])
                bucket_display_df = bucket_df.copy()
                bucket_display_df["漲跌%"] = bucket_display_df["漲跌%"].apply(format_color)
                bucket_display_df["K值"] = bucket_display_df["K值"].apply(format_k)
                bucket_display_df["成交量(張)"] = bucket_display_df["成交量(張)"].apply(format_volume)
                bucket_display_df["跳空訊號"] = bucket_display_df["跳空訊號"].apply(format_gap)
                bucket_display_df["代碼"] = bucket_display_df["代碼網址"]
                for col in ["MA位置", "MA排列"]:
                    if col not in bucket_display_df.columns:
                        bucket_display_df[col] = "-"
                st.dataframe(bucket_display_df[signal_columns], use_container_width=True, column_config={
                    "代碼": st.column_config.LinkColumn("代碼", help="點擊前往台股 Yahoo", display_text=r"https://tw.stock.yahoo.com/quote/(.*)"),
                    "股票名稱": st.column_config.TextColumn("股票名稱")
                })
            else:
                st.caption(f"目前沒有符合「{display_name}」的股票。")
else:
    st.info("目前沒有掃描到符合勾選條件的股票。")

st.divider()
render_summary_dashboard(group_up_summary, rise_threshold)
st.divider()

for group_name, info in group_tables.items():
    anchor_id = make_anchor_id(group_name)
    st.markdown(f'<div id="{anchor_id}" style="scroll-margin-top: 80px;"></div>', unsafe_allow_html=True)
    header_col1, header_col2 = st.columns([8, 2])
    with header_col1: st.subheader(f"【{group_name}】({info['count']}檔)")
    with header_col2: st.markdown("""<div style="text-align:right; padding-top:0.4rem;"><a href="#dashboard-top" class="back-to-dashboard-btn">⬆ 回到儀表板</a></div>""", unsafe_allow_html=True)
    table_df = info["table"].copy()
    if not table_df.empty and "代碼網址" in table_df.columns: table_df["代碼"] = table_df["代碼網址"]
    display_columns = ["代碼", "股票名稱", "價格", "漲跌%", "成交量(張)", "MA位置", "MA排列", "K值", "D值", "KD訊號", "MACD柱", "MACD訊號", "跳空訊號", "訊號類型", "來源"]
    st.dataframe(table_df[display_columns], use_container_width=True, column_config={
        "代碼": st.column_config.LinkColumn("代碼", help="點擊前往台股 Yahoo", display_text=r"https://tw.stock.yahoo.com/quote/(.*)"),
        "股票名稱": st.column_config.TextColumn("股票名稱")
    })
    st.markdown('<div style="margin-bottom: 10px;"></div>', unsafe_allow_html=True)

if (st.session_state.auto_refresh_enabled and not st.session_state.group_editor_unlocked and not st.session_state.editing_mode):
    refresh_sec = max(1, int(st.session_state.get("refresh_sec", REFRESH_SEC)))
    time.sleep(refresh_sec)
    st.rerun()
