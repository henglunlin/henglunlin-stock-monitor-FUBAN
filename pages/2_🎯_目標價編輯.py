"""
🎯 目標價編輯
=============
專門用來設定「買入區間 + 停損價」目標價的獨立編輯頁面，取代舊版分組編輯文字框裡的
buy=/sell= 語法。跟「🛠️ 訊號編輯」頁面不同：那個頁面編輯的是「全體股票共用」的訊號
判斷門檻（模組層級具名常數），這裡編輯的是「每一檔股票各自不同」的目標價設定，兩者是
不同性質的資料，所以分開做成獨立頁面，不硬塞進同一套參數面板機制。

資料存在 target_price_list.json（跟 stock_groups.json 是各自獨立的檔案），格式：
    {
        "2330.TW": {"target_price": 2200, "low_pct": 5, "high_pct": 5, "stop_loss": 2000},
        ...
    }
只存「原始輸入」四個欄位（中心價、買入價格(L)%、買入價格(U)%、停損價格），不存算好的
買入區間絕對值上下限——但下方表格「買入價格(L)」「買入價格(U)」兩欄可以直接輸入絕對值，
輸入後會自動反推對應的百分比並存成 %（畫面上兩種表示法互相同步，方便你想用哪種就用哪種，
JSON 檔案裡永遠只存 % 版本）。

判斷邏輯（要不要跳 toast / 進 Telegram 彙整訊息）在 signal_module/target_price.py，
monitor 主程式 app.py 的 render_live_monitor() 每次刷新都會讀這份 JSON 查表。

PIN 碼：跟股票分組編輯共用同一組（GROUP_EDIT_PIN），這裡獨立輸入、獨立解鎖狀態
（app.py 的分組編輯鎖跟這裡的鎖是兩個頁面各自的 session_state，不互通，每個頁面
第一次要編輯時都要各自輸入一次 PIN）。

⚠️ Streamlit 的 pages/ 頁面彼此無法直接 import 對方（app.py 頂層有會直接執行的
Streamlit 呼叫，import 進來會整個跑一次），所以這裡的 PIN 碼、GitHub 設定、股票代碼
正規化等基礎工具函式，是跟 app.py 各自獨立的一份（比照現有 1_🛠️_signal editor.py
頁面的既有作法）。signal_module/ 是純資料/邏輯的 package，沒有這個問題，所以買入區間
的計算公式（compute_buy_zone）直接從 signal_module.target_price import，不重複寫一份，
避免跟 app.py／signal_module 兩邊的公式將來改到不一致。
"""
import base64
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

from signal_module.target_price import compute_buy_zone

st.set_page_config(page_title="目標價編輯", layout="wide")

TW_TZ = ZoneInfo("Asia/Taipei")

# 跟 app.py 同一組 PIN（使用者已確認：分組編輯、目標價編輯共用同一組 PIN）。
GROUP_EDIT_PIN = "1219"

TARGET_PRICE_FILE = "target_price_list.json"
TARGET_PRICE_BACKUP_DIR = "target_price_backups"
STOCK_NAME_FILE = "TWstocklistname2.txt"

COLUMNS = ["啟用", "代碼", "股票名稱", "目標買入價格", "買入價格(L)%", "買入價格(U)%", "買入價格(L)", "買入價格(U)", "停損價格"]


# =============================================================================
# 基礎工具（跟 app.py 各自獨立的一份，邏輯需保持同步）
# =============================================================================
def get_secret_or_default(key: str, default: str = ""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default


def github_repo_config():
    """monitor 這個 repo 自己的設定 (henglunlin-stock-monitor-FUBAN)。
    target_price_list.json 只有這個監控 app 自己會讀，不需要像 stock_groups.json
    那樣同時推到 stock-scanner-FUBAN。
    """
    return {
        "token": get_secret_or_default("GITHUB_TOKEN", ""),
        "owner": get_secret_or_default("GITHUB_OWNER", "henglunlin"),
        "repo": get_secret_or_default("GITHUB_REPO", "henglunlin-stock-monitor-FUBAN"),
        "branch": get_secret_or_default("GITHUB_BRANCH", "main"),
    }


def upload_file_to_github(file_bytes: bytes, github_path: str, commit_message: str) -> bool:
    cfg = github_repo_config()
    token, owner, repo, branch = cfg["token"], cfg["owner"], cfg["repo"], cfg["branch"]
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


def fetch_target_price_list_from_github() -> dict:
    cfg = github_repo_config()
    url = f"https://raw.githubusercontent.com/{cfg['owner']}/{cfg['repo']}/{cfg['branch']}/target_price_list.json"
    res = requests.get(url, timeout=15)
    res.raise_for_status()
    data = res.json()
    return validate_and_normalize_target_price_json(data)


def upload_target_price_list_to_github(data: dict, commit_message: str = "Update target_price_list.json via 目標價編輯頁面") -> bool:
    content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    return upload_file_to_github(content, "target_price_list.json", commit_message)


def symbol_to_code(symbol: str) -> str:
    return str(symbol).strip().upper().split(".")[0]


@st.cache_data(ttl=86400)
def load_stock_lookup_maps(file_path: str = STOCK_NAME_FILE) -> dict:
    code_to_name = {}
    code_to_symbol = {}
    if not os.path.exists(file_path):
        return {"code_to_name": code_to_name, "code_to_symbol": code_to_symbol}
    with open(file_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            line = line.replace("﻿", "").replace("　", " ").strip()
            if "\t" in line:
                parts = [p.strip() for p in line.split("\t") if p.strip()]
            else:
                m = re.match(r"^([^\s]+)\s+(.+)$", line)
                parts = [m.group(1).strip(), m.group(2).strip()] if m else []
            if len(parts) < 2:
                continue
            raw_symbol = parts[0].upper()
            stock_name = parts[1].strip()
            code = symbol_to_code(raw_symbol)
            if not code or not stock_name:
                continue
            code_to_name[code] = stock_name
            code_to_symbol[code] = raw_symbol
    return {"code_to_name": code_to_name, "code_to_symbol": code_to_symbol}


def normalize_symbol_quick(input_text: str):
    """把使用者輸入的代碼正規化成完整 ticker（例如 "2330" -> "2330.TW"）。
    查不到對照表時，原樣（大寫、去空白）傳回，不會擋住存檔——存檔後 signal_module 那邊
    查表比對不到就是「未設定目標價」，不影響其他股票，但建議之後補齊 TWstocklistname2.txt。
    """
    s = str(input_text).strip().upper()
    if not s:
        return ""
    if "." in s:
        return s
    if s.isdigit():
        lookup = load_stock_lookup_maps(STOCK_NAME_FILE)
        code_to_symbol = lookup.get("code_to_symbol", {})
        if s in code_to_symbol:
            return code_to_symbol[s]
    return s


def get_stock_name(symbol: str) -> str:
    lookup = load_stock_lookup_maps(STOCK_NAME_FILE)
    return lookup.get("code_to_name", {}).get(symbol_to_code(symbol), "")


# =============================================================================
# target_price_list.json 讀寫（跟 app.py 各自獨立的一份，邏輯需保持同步）
# =============================================================================
def _to_bool(value, default=True):
    """把各種可能的原始輸入正規化成布林值；沒有這個 key（value 為 None）時視為 default
    （相容沒有 enabled 欄位的既有 target_price_list.json，一律預設開啟）。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off", "")
    return default


def _normalize_target_price_entry(raw: dict):
    if not isinstance(raw, dict):
        return None
    try:
        target_price = float(raw.get("target_price"))
    except (TypeError, ValueError):
        return None
    if target_price <= 0:
        return None

    def _pct(value, default=5.0):
        try:
            v = float(value)
        except (TypeError, ValueError):
            return default
        return v if v >= 0 else default

    low_pct = _pct(raw.get("low_pct"), 5.0)
    high_pct = _pct(raw.get("high_pct"), 5.0)

    stop_loss_raw = raw.get("stop_loss")
    stop_loss = None
    if stop_loss_raw not in (None, ""):
        try:
            sl = float(stop_loss_raw)
            if sl > 0:
                stop_loss = sl
        except (TypeError, ValueError):
            stop_loss = None

    return {
        "target_price": target_price,
        "low_pct": low_pct,
        "high_pct": high_pct,
        "stop_loss": stop_loss,
        "enabled": _to_bool(raw.get("enabled"), True),
    }


def validate_and_normalize_target_price_json(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("JSON 格式錯誤：最外層必須是物件（dict）")
    validated = {}
    for symbol, raw in data.items():
        symbol = str(symbol).strip().upper()
        if not symbol:
            continue
        entry = _normalize_target_price_entry(raw)
        if entry is not None:
            validated[symbol] = entry
    return validated


def load_target_price_list() -> dict:
    if os.path.exists(TARGET_PRICE_FILE):
        try:
            with open(TARGET_PRICE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return validate_and_normalize_target_price_json(data)
        except Exception:
            pass
    return {}


def save_target_price_list(data: dict):
    with open(TARGET_PRICE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_target_price_backup_dir():
    os.makedirs(TARGET_PRICE_BACKUP_DIR, exist_ok=True)


def save_target_price_backup_snapshot(data: dict):
    ensure_target_price_backup_dir()
    tw_now = datetime.now(TW_TZ)
    filename = f"target_price_list_backup_{tw_now.strftime('%Y%m%d_%H%M%S')}.json"
    file_path = os.path.join(TARGET_PRICE_BACKUP_DIR, filename)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return file_path


def list_target_price_backup_files():
    if not os.path.exists(TARGET_PRICE_BACKUP_DIR):
        return []
    files = []
    for name in os.listdir(TARGET_PRICE_BACKUP_DIR):
        if name.lower().endswith(".json"):
            full_path = os.path.join(TARGET_PRICE_BACKUP_DIR, name)
            if os.path.isfile(full_path):
                files.append((name, os.path.getmtime(full_path)))
    files.sort(key=lambda x: x[1], reverse=True)
    return [name for name, _ in files]


def persist_target_price_list(data: dict):
    """存檔統一入口：先存本機（同時更新 st.session_state.target_price_list，
    讓 app.py 監控主迴圈立刻讀到最新設定，不用重開程式），有勾選同步才推 GitHub。
    """
    save_target_price_list(data)
    st.session_state.target_price_list = data
    if st.session_state.get("sync_target_price_to_github", False):
        if upload_target_price_list_to_github(data):
            st.success("已同步更新到 GitHub 的 target_price_list.json。")
        else:
            st.warning("同步 GitHub 失敗，請確認 Secrets 中的 GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO 設定。")


# =============================================================================
# JSON <-> 表格列 互轉
# =============================================================================
def dict_to_rows(data: dict) -> list:
    rows = []
    for symbol, cfg in sorted(data.items()):
        center = float(cfg.get("target_price", 0) or 0)
        low_pct = float(cfg.get("low_pct", 5) or 5)
        high_pct = float(cfg.get("high_pct", 5) or 5)
        stop_loss = cfg.get("stop_loss")
        lower, upper = (compute_buy_zone(center, low_pct, high_pct) if center > 0 else (None, None))
        rows.append({
            "啟用": _to_bool(cfg.get("enabled"), True),
            "代碼": symbol_to_code(symbol),
            "股票名稱": get_stock_name(symbol),
            "目標買入價格": center if center > 0 else None,
            "買入價格(L)%": low_pct,
            "買入價格(U)%": high_pct,
            "買入價格(L)": round(lower, 2) if lower is not None else None,
            "買入價格(U)": round(upper, 2) if upper is not None else None,
            "停損價格": stop_loss,
        })
    return rows


def rows_to_dict(rows: list) -> dict:
    """轉回存檔用的 JSON：只取原始輸入四欄，代碼正規化成完整 ticker，
    代碼空白或目標買入價格未填的列直接跳過（視為尚未填完整、不存檔）。
    """
    result = {}
    for row in rows:
        code_raw = str(row.get("代碼") or "").strip()
        if not code_raw:
            continue
        symbol = normalize_symbol_quick(code_raw)
        if not symbol:
            continue
        center = row.get("目標買入價格")
        if center is None or (isinstance(center, float) and pd.isna(center)):
            continue
        try:
            center = float(center)
        except (TypeError, ValueError):
            continue
        if center <= 0:
            continue

        def _num(value, default):
            if value is None:
                return default
            try:
                if isinstance(value, float) and pd.isna(value):
                    return default
                return float(value)
            except (TypeError, ValueError):
                return default

        low_pct = _num(row.get("買入價格(L)%"), 5.0)
        high_pct = _num(row.get("買入價格(U)%"), 5.0)
        stop_loss_val = row.get("停損價格")
        stop_loss = None
        if stop_loss_val is not None and not (isinstance(stop_loss_val, float) and pd.isna(stop_loss_val)):
            try:
                sl = float(stop_loss_val)
                if sl > 0:
                    stop_loss = sl
            except (TypeError, ValueError):
                stop_loss = None

        result[symbol] = {
            "target_price": center,
            "low_pct": max(low_pct, 0.0),
            "high_pct": max(high_pct, 0.0),
            "stop_loss": stop_loss,
            "enabled": _to_bool(row.get("啟用"), True),
        }
    return result


def _recompute_row(row: dict, changed_cols: set) -> dict:
    """買入區間絕對值 <-> % 雙向同步：改中心價/%就重算絕對值；改絕對值就反推%。"""
    try:
        center = float(row.get("目標買入價格")) if row.get("目標買入價格") not in (None, "") else 0.0
        if isinstance(row.get("目標買入價格"), float) and pd.isna(row.get("目標買入價格")):
            center = 0.0
    except (TypeError, ValueError):
        center = 0.0

    if center <= 0:
        row["買入價格(L)"] = None
        row["買入價格(U)"] = None
        return row

    def _safe_float(value, default):
        try:
            if value is None:
                return default
            if isinstance(value, float) and pd.isna(value):
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    low_pct = _safe_float(row.get("買入價格(L)%"), 5.0)
    high_pct = _safe_float(row.get("買入價格(U)%"), 5.0)

    if {"目標買入價格", "買入價格(L)%", "買入價格(U)%"} & changed_cols:
        lower, upper = compute_buy_zone(center, low_pct, high_pct)
        row["買入價格(L)"] = round(lower, 2)
        row["買入價格(U)"] = round(upper, 2)

    if "買入價格(L)" in changed_cols:
        lower_abs = _safe_float(row.get("買入價格(L)"), None)
        if lower_abs is not None:
            row["買入價格(L)%"] = round((center - lower_abs) / center * 100, 4)

    if "買入價格(U)" in changed_cols:
        upper_abs = _safe_float(row.get("買入價格(U)"), None)
        if upper_abs is not None:
            row["買入價格(U)%"] = round((upper_abs - center) / center * 100, 4)

    return row


# =============================================================================
# PIN 鎖
# =============================================================================
if "tp_editor_unlocked" not in st.session_state:
    st.session_state.tp_editor_unlocked = False
if "target_price_list" not in st.session_state:
    st.session_state.target_price_list = load_target_price_list()
if "tp_rows" not in st.session_state:
    st.session_state.tp_rows = dict_to_rows(st.session_state.target_price_list)
if "tp_edit_version" not in st.session_state:
    st.session_state.tp_edit_version = 0

st.title("🎯 目標價編輯")
st.caption("設定每一檔股票的「買入區間」（中心價 ± 各自的百分比）與「停損價格」。存檔後 app.py 監控主迴圈下次刷新就會套用。")

if not st.session_state.tp_editor_unlocked:
    st.info("這個頁面跟股票分組編輯共用同一組 PIN 碼。")
    pin_input = st.text_input("請輸入 PIN 碼以編輯目標價", type="password", key="tp_pin_input")
    if st.button("解鎖編輯", key="tp_unlock_btn"):
        if pin_input == GROUP_EDIT_PIN:
            st.session_state.tp_editor_unlocked = True
            st.success("PIN 正確，已解鎖")
            st.rerun()
        else:
            st.error("PIN 錯誤")
    st.stop()

lock_col1, lock_col2 = st.columns([1, 5])
with lock_col1:
    if st.button("🔒 鎖定編輯", key="tp_lock_btn"):
        st.session_state.tp_editor_unlocked = False
        st.rerun()

st.checkbox(
    "☁️ 存檔時同步提交到 GitHub",
    value=st.session_state.get("sync_target_price_to_github", False),
    key="sync_target_price_to_github",
    help="需要在 Secrets 設定 GITHUB_TOKEN（且 GITHUB_OWNER/GITHUB_REPO/GITHUB_BRANCH 正確），"
         "否則勾選了也只會存在本機（下次重新部署會遺失）。只會推送到監控 app 自己的 repo。",
)

st.divider()

# =============================================================================
# 表格編輯
# =============================================================================
st.markdown("### 📋 目標價設定表")
st.caption(
    "「買入價格(L)%」「買入價格(U)%」跟「買入價格(L)」「買入價格(U)」是同一件事的兩種表示法，"
    "改任何一邊，另一邊都會自動重新計算並更新畫面顯示；存檔時 JSON 只會存 % 版本。"
    "「代碼」可以填純數字（例如 2330）或完整 ticker（例如 2330.TW）。"
    "要新增一列，把游標移到表格最後一列下方的空白列直接輸入即可；要刪除，選取整列後按 Delete。"
)

editor_key = f"tp_data_editor_v{st.session_state.tp_edit_version}"
display_df = pd.DataFrame(st.session_state.tp_rows, columns=COLUMNS)

edited_df = st.data_editor(
    display_df,
    key=editor_key,
    num_rows="dynamic",
    hide_index=True,
    width="stretch",
    column_config={
        "啟用": st.column_config.CheckboxColumn("啟用", help="關閉後，這檔股票即使符合買入區間/停損條件也不會有任何提醒（toast、Telegram 彙整訊息都不會出現，買賣訊號欄位也不會顯示）", default=True),
        "代碼": st.column_config.TextColumn("代碼", help="股票代碼，例如 2330 或 2330.TW"),
        "股票名稱": st.column_config.TextColumn("股票名稱", disabled=True, help="自動查表顯示，不可編輯；存檔後、或代碼變動後才會更新"),
        "目標買入價格": st.column_config.NumberColumn("目標買入價格", help="買入區間的中心價", min_value=0.0, format="%.2f"),
        "買入價格(L)%": st.column_config.NumberColumn("買入價格(L)%", help="下緣百分比，預設 5", min_value=0.0, format="%.2f"),
        "買入價格(U)%": st.column_config.NumberColumn("買入價格(U)%", help="上緣百分比，預設 5", min_value=0.0, format="%.2f"),
        "買入價格(L)": st.column_config.NumberColumn("買入價格(L)", help="買入區間下緣絕對值，可直接編輯（會自動反推對應的 %）", format="%.2f"),
        "買入價格(U)": st.column_config.NumberColumn("買入價格(U)", help="買入區間上緣絕對值，可直接編輯（會自動反推對應的 %）", format="%.2f"),
        "停損價格": st.column_config.NumberColumn("停損價格", help="選填，留空代表不設定停損", min_value=0.0, format="%.2f"),
    },
)

editor_state = st.session_state.get(editor_key, {})
edited_rows = editor_state.get("edited_rows", {})
added_rows = editor_state.get("added_rows", [])
deleted_rows = editor_state.get("deleted_rows", [])

if edited_rows or added_rows or deleted_rows:
    # edited_rows / deleted_rows 的 row_idx 是「原始輸入表格」的 index（也就是
    # st.session_state.tp_rows 當時的順序），跟 data_editor 回傳的 edited_df（已經套用
    # 新增/刪除後的最終畫面）不是同一套 index，兩者不能混用；所以編輯先套用在原始列表上，
    # 刪除也用原始 index 過濾，最後才把「新增列」從 edited_df 的尾端取出附加
    # （Streamlit 的 num_rows="dynamic" 一律把新增列放在最後面，不受刪除影響，可以安全取用）。
    base_rows = [dict(r) for r in st.session_state.tp_rows]

    for row_idx_str, changes in edited_rows.items():
        row_idx = int(row_idx_str) if isinstance(row_idx_str, str) else row_idx_str
        if 0 <= row_idx < len(base_rows):
            base_rows[row_idx].update(changes)
            base_rows[row_idx] = _recompute_row(base_rows[row_idx], set(changes.keys()))

    deleted_set = set(deleted_rows)
    working_rows = [r for i, r in enumerate(base_rows) if i not in deleted_set]

    if added_rows:
        edited_records = edited_df.to_dict("records")
        new_tail = edited_records[len(edited_records) - len(added_rows):]
        for record, added in zip(new_tail, added_rows):
            row = dict(record)
            if row.get("啟用") is None or (isinstance(row.get("啟用"), float) and pd.isna(row.get("啟用"))):
                row["啟用"] = True
            if row.get("買入價格(L)%") in (None, "") or (isinstance(row.get("買入價格(L)%"), float) and pd.isna(row.get("買入價格(L)%"))):
                row["買入價格(L)%"] = 5.0
            if row.get("買入價格(U)%") in (None, "") or (isinstance(row.get("買入價格(U)%"), float) and pd.isna(row.get("買入價格(U)%"))):
                row["買入價格(U)%"] = 5.0
            row = _recompute_row(row, {"目標買入價格", "買入價格(L)%", "買入價格(U)%"} | set(added.keys()))
            working_rows.append(row)

    st.session_state.tp_rows = working_rows
    st.session_state.tp_edit_version += 1
    st.rerun()

st.divider()

save_col1, save_col2 = st.columns([1, 4])
with save_col1:
    if st.button("💾 儲存目標價設定", key="tp_save_btn", width="stretch"):
        new_data = rows_to_dict(st.session_state.tp_rows)
        try:
            save_target_price_backup_snapshot(st.session_state.target_price_list)
        except Exception:
            pass
        persist_target_price_list(new_data)

        # 非阻擋式提醒：停損價格如果比買入區間下緣還高，可能是打反或設定矛盾，但不擋存檔。
        warnings = []
        for symbol, cfg in new_data.items():
            if cfg.get("stop_loss") is None:
                continue
            lower, _upper = compute_buy_zone(cfg["target_price"], cfg["low_pct"], cfg["high_pct"])
            if cfg["stop_loss"] > lower:
                warnings.append(f"{symbol_to_code(symbol)}：停損價格 {cfg['stop_loss']:.2f} 比買入區間下緣 {lower:.2f} 還高")
        if warnings:
            st.warning("以下股票的停損價格設定可能有問題（已照樣存檔，僅供提醒）：\n" + "\n".join(f"- {w}" for w in warnings))

        st.session_state.tp_rows = dict_to_rows(new_data)
        st.session_state.tp_edit_version += 1
        st.success(f"已儲存，共 {len(new_data)} 檔股票設定了目標價。")
        st.rerun()
with save_col2:
    st.caption("存檔後才會寫入 target_price_list.json、更新 app.py 監控主迴圈讀到的設定；表格上的即時試算不會自動存檔。")

st.divider()

# =============================================================================
# GitHub 同步 / 備份 / 匯出匯入 / 重設
# =============================================================================
with st.expander("☁️ GitHub 同步", expanded=False):
    st.caption(f"repo：{github_repo_config()['owner']}/{github_repo_config()['repo']}（分支：{github_repo_config()['branch']}）")
    gh_col1, gh_col2 = st.columns(2)
    with gh_col1:
        if st.button("📥 從 GitHub 讀取最新 target_price_list.json", key="tp_pull_github_btn", width="stretch"):
            try:
                fetched = fetch_target_price_list_from_github()
                save_target_price_backup_snapshot(st.session_state.target_price_list)
                st.session_state.target_price_list = fetched
                save_target_price_list(fetched)  # 從 GitHub 讀來的，只存本機快取，不用再推回去
                st.session_state.tp_rows = dict_to_rows(fetched)
                st.session_state.tp_edit_version += 1
                st.success("已從 GitHub 讀取最新目標價設定。")
                st.rerun()
            except Exception as e:
                st.error(f"從 GitHub 讀取失敗：{e}")
    with gh_col2:
        if st.button("☁️ 手動推送目前設定到 GitHub", key="tp_push_github_btn", width="stretch"):
            if upload_target_price_list_to_github(st.session_state.target_price_list):
                st.success("已推送到 GitHub。")
            else:
                st.warning("推送失敗，請確認 Secrets 中的 GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO 設定。")

with st.expander("📦 備份 / 匯出 / 匯入 JSON", expanded=False):
    export_json_str = json.dumps(st.session_state.target_price_list, ensure_ascii=False, indent=2)
    st.download_button(
        label="⬇️ 匯出目前目標價設定 JSON", data=export_json_str,
        file_name="target_price_list.json", mime="application/json",
        key="tp_download_json_btn", width="stretch",
    )
    if st.button("🗂️ 建立本地備份", key="tp_create_backup_btn", width="stretch"):
        try:
            backup_file = save_target_price_backup_snapshot(st.session_state.target_price_list)
            st.success(f"已建立備份：{os.path.basename(backup_file)}")
        except Exception as e:
            st.error(f"建立備份失敗：{e}")
    uploaded_file = st.file_uploader("上傳 target_price_list JSON", type=["json"], key="tp_upload_json_file")
    if uploaded_file is not None:
        st.caption("上傳後按下「匯入並覆蓋目前設定」才會生效")
        if st.button("📥 匯入並覆蓋目前設定", key="tp_import_json_btn", width="stretch"):
            try:
                raw = uploaded_file.read()
                data = json.loads(raw.decode("utf-8"))
                validated = validate_and_normalize_target_price_json(data)
                save_target_price_backup_snapshot(st.session_state.target_price_list)
                persist_target_price_list(validated)
                st.session_state.tp_rows = dict_to_rows(validated)
                st.session_state.tp_edit_version += 1
                st.success("JSON 匯入成功，已覆蓋目前目標價設定")
                st.rerun()
            except Exception as e:
                st.error(f"JSON 匯入失敗：{e}")
    backups = list_target_price_backup_files()
    if backups:
        st.markdown("**最近備份檔**")
        for name in backups[:5]:
            st.caption(name)
    else:
        st.caption("目前沒有本地備份檔")

with st.expander("♻️ 清空全部目標價設定", expanded=False):
    st.caption("會先自動備份目前設定，再清空。清空後所有股票都會變成「未設定目標價」，signal_module 不會再判斷任何目標價訊號。")
    if st.button("🗑️ 清空全部", key="tp_reset_btn", width="stretch"):
        try:
            save_target_price_backup_snapshot(st.session_state.target_price_list)
        except Exception:
            pass
        persist_target_price_list({})
        st.session_state.tp_rows = []
        st.session_state.tp_edit_version += 1
        st.success("已清空全部目標價設定。")
        st.rerun()
