"""
🛠️ 訊號編輯
=============
瀏覽 / 編輯 signal_module/ 底下的訊號規則檔案。

存檔後：
1. 立即在「本次」執行的伺服器上生效（呼叫 module_loader 重新載入訊號登記表）
2. 可選擇「同時提交到 GitHub」，讓下次部署 / 重新啟動時也不會遺失變更
   （沿用主程式已經在使用的 GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO / GITHUB_BRANCH secrets）QA

參數面板 (2026-08-13 新增)：
- 針對「全大寫命名、單純數字」的模組層級具名常數（例如 RISE_THRESHOLD_PCT = 9.5），
  自動掃描出來、顯示成滑桿/數字輸入框，方便不看程式碼也能快速調整訊號門檻。
- 調整面板數值，會直接同步改寫下方「原始碼」文字框裡對應那一行的內容，
  兩邊共用同一顆「💾 儲存並重新載入」按鈕與「同時提交到 GitHub」勾選框，
  存檔行為與原本的原始碼編輯器完全一致。
- 同一個常數名稱在檔案中出現超過一次、或值不是單純數字常值（例如表達式、變數運算），
  會自動略過、不列入面板，仍可在下方原始碼編輯器手動修改。
"""
import base64
import os
import re

import pandas as pd
import requests
import streamlit as st

from signal_module import module_loader
from signal_module.base import SIGNAL_REGISTRY

st.set_page_config(layout="wide")
st.title("🛠️ 訊號編輯")
st.caption("每一個檔案都是一個獨立的訊號判斷規則。改完程式碼、按下「儲存並重新載入」即可立即套用到掃描器，不用重新部署。")

SIGNAL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "signal_module"
)
EXCLUDE_FILES = {"__init__.py", "base.py", "indicators.py", "module_loader.py"}

# ===== 參數面板：具名常數掃描/改寫 =====
# 只認「模組層級、全大寫命名、純數字常值」的賦值行，例如：
#   RISE_THRESHOLD_PCT = 9.5
#   LOOKBACK_DAYS = 22        # 往前找幾天
# 不吃表達式、函式呼叫、字串、tuple 等，避免誤判/誤寫壞語法。
CONST_LINE_RE = re.compile(
    r'^(?P<name>[A-Z][A-Z0-9_]*)(?P<eq>\s*=\s*)(?P<value>-?\d+(?:\.\d+)?)(?P<tail>\s*(?:#.*)?)$'
)


def parse_editable_constants(content: str):
    """掃描原始碼，回傳 {name: (raw_value_str, comment_text)}。
    同一個名字出現超過一次的，一律排除(不列入面板)，避免改寫時改錯行或改到不該改的那一個。
    """
    counts = {}
    raw_values = {}
    comments = {}
    for line in content.splitlines():
        m = CONST_LINE_RE.match(line)
        if not m:
            continue
        name = m.group("name")
        counts[name] = counts.get(name, 0) + 1
        raw_values[name] = m.group("value")
        comment = (m.group("tail") or "").strip()
        comments[name] = comment[1:].strip() if comment.startswith("#") else ""
    return {name: raw_values[name] for name, c in counts.items() if c == 1}, comments


def format_constant_value(new_value: float, was_float: bool) -> str:
    """把面板調整後的數值格式化回原始碼字面量字串，盡量維持簡潔（不留多餘的0）。"""
    if not was_float:
        return str(int(round(new_value)))
    text = f"{new_value:.4f}".rstrip("0")
    if text.endswith("."):
        text += "0"
    return text


def apply_constant_change(content: str, name: str, new_value_str: str) -> str:
    """把 content 裡 `name = <數字>` 這一行的數字部分改成 new_value_str，其餘(含註解)不動。"""
    pattern = re.compile(
        rf'^({re.escape(name)}\s*=\s*)(-?\d+(?:\.\d+)?)(\s*(?:#.*)?)$', re.MULTILINE
    )
    return pattern.sub(lambda m: f"{m.group(1)}{new_value_str}{m.group(3)}", content, count=1)


# ===== 沿用主程式既有的 GitHub 上傳方式 =====
def github_repo_config():
    return {
        "token": st.secrets.get("GITHUB_TOKEN", ""),
        "owner": st.secrets.get("GITHUB_OWNER", "henglunlin"),
        "repo": st.secrets.get("GITHUB_REPO", "henglunlin-stock-monitor-FUBAN"),
        "branch": st.secrets.get("GITHUB_BRANCH", "main"),
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


def list_signal_files():
    if not os.path.isdir(SIGNAL_DIR):
        return []
    return [f for f in sorted(os.listdir(SIGNAL_DIR)) if f.endswith(".py") and f not in EXCLUDE_FILES]


# ===== 頂部工具列 =====
top_col1, top_col2 = st.columns([1, 3])
with top_col1:
    if st.button("🔄 重新載入所有訊號", use_container_width=True):
        _, errors = module_loader.load_default_signal_modules()
        if errors:
            st.error("部分訊號載入失敗：\n" + "\n".join(errors))
        else:
            st.success(f"已重新載入，目前共 {len(SIGNAL_REGISTRY)} 個訊號。")

st.divider()

# ===== 目前已註冊的訊號清單 =====
st.markdown("### 📋 目前已註冊的訊號")
summary_rows = [
    {
        "檔名": key,
        "訊號名稱": cfg["label"],
        "買賣方向": "🟢 買進" if cfg.get("kind", "buy") == "buy" else "🔴 賣出/風險",
        "規則說明": cfg.get("description", ""),
    }
    for key, cfg in SIGNAL_REGISTRY.items()
]
st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

st.divider()

# ===== 編輯既有訊號檔案 =====
st.markdown("### ✏️ 編輯訊號檔案")

files = list_signal_files()
if not files:
    st.info(f"在 {SIGNAL_DIR} 找不到任何訊號檔案。")
else:
    picked_file = st.selectbox("選擇要編輯的訊號檔案", options=files, key="signal_edit_picked_file")
    file_path = os.path.join(SIGNAL_DIR, picked_file)
    with open(file_path, "r", encoding="utf-8") as f:
        original_content = f.read()

    editor_key = f"editor_{picked_file}"
    sync_snapshot_key = f"_panel_synced_{picked_file}"

    # 目前「原始碼」內容的基準版本：如果 text_area 之前已經被建立過(session_state裡有)，
    # 就以它目前的內容為準(可能是使用者手動編輯過的)；否則用剛從檔案讀到的內容。
    base_content = st.session_state.get(editor_key, original_content)

    # 若基準內容跟「上次面板同步完」的快照不一致，代表原始碼是被外部改動的
    # (例如使用者直接在下方文字框手動編輯、或切換了檔案、或重新整理頁面)，
    # 這時要把面板各個小工具的 session_state 清掉，讓面板重新依照目前原始碼的數值顯示，
    # 避免面板顯示的還是舊的、已經跟原始碼不同步的數字。
    if st.session_state.get(sync_snapshot_key) != base_content:
        for k in list(st.session_state.keys()):
            if k.startswith(f"panel_val_{picked_file}::"):
                del st.session_state[k]

    editable_constants, comments = parse_editable_constants(base_content)

    # ===== 🎛️ 參數面板 (預設收起) =====
    with st.expander("🎛️ 參數面板（快速調整具名常數，不用直接看程式碼）", expanded=False):
        if not editable_constants:
            st.caption(
                "此檔案沒有偵測到可調整的具名常數"
                "（全大寫命名 + 純數字常值 + 只出現一次），"
                "可能本來就沒有寫死的門檻值，或該常數目前被排除(重複定義/非純數字)。"
                "仍可在下方「原始碼」直接修改。"
            )
        else:
            st.caption("拖動/輸入數值即可即時同步到下方原始碼；存檔請用下方「💾 儲存並重新載入」按鈕。")
            working_content = base_content
            for name, raw_val in editable_constants.items():
                was_float = "." in raw_val
                widget_key = f"panel_val_{picked_file}::{name}"
                help_text = comments.get(name) or None

                if was_float:
                    current_val = float(raw_val)
                    step = 0.5 if abs(current_val) >= 10 else 0.1
                    new_val = st.number_input(
                        name, value=current_val, step=step, format="%.2f",
                        help=help_text, key=widget_key,
                    )
                else:
                    current_val = int(raw_val)
                    new_val = st.number_input(
                        name, value=current_val, step=1,
                        help=help_text, key=widget_key,
                    )

                new_val_str = format_constant_value(float(new_val), was_float)
                # 只有數值真的變了才需要改寫這一行，避免無意義的字串重排(例如 9.50 vs 9.5)
                if format_constant_value(current_val, was_float) != new_val_str:
                    working_content = apply_constant_change(working_content, name, new_val_str)

            if working_content != base_content:
                base_content = working_content
                # 面板改的內容，必須在下面 st.text_area 建立"之前"寫回 session_state，
                # 這樣 text_area 才會顯示面板同步後的最新內容。
                st.session_state[editor_key] = base_content

    st.session_state[sync_snapshot_key] = base_content

    edited_content = st.text_area(
        "原始碼", value=base_content, height=480, key=editor_key,
    )

    also_push_github = st.checkbox(
        "同時提交到 GitHub（下次部署/重啟才不會遺失變更；需在 Secrets 設定 GITHUB_TOKEN）",
        value=False, key=f"push_github_{picked_file}",
    )

    save_col1, _ = st.columns([1, 3])
    with save_col1:
        if st.button("💾 儲存並重新載入", key=f"save_{picked_file}", use_container_width=True):
            try:
                compile(edited_content, picked_file, "exec")
            except SyntaxError as e:
                st.error(f"語法錯誤，未儲存：{e}")
            else:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(edited_content)
                _, errors = module_loader.load_default_signal_modules()
                if errors:
                    st.error("儲存成功，但重新載入時發生錯誤：\n" + "\n".join(errors))
                else:
                    st.success(f"已儲存並重新載入 {picked_file}！")

                if also_push_github:
                    ok = upload_file_to_github(
                        edited_content.encode("utf-8"),
                        f"signal_module/{picked_file}",
                        f"Update signal_module/{picked_file} via 訊號編輯頁面",
                    )
                    if ok:
                        st.success("已提交到 GitHub。")
                    else:
                        st.warning("提交到 GitHub 失敗，請確認 Secrets 中的 GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO 設定。")
                st.rerun()

st.divider()

# ===== 新增自訂訊號檔案 =====
st.markdown("### ➕ 新增自訂訊號檔案")
st.caption("上傳一個新的 .py 檔案（需依照 signal_module/base.py 的規範撰寫，並使用 @register_signal 裝飾器），存入後會自動重新載入。")

uploaded_signal_file = st.file_uploader("上傳訊號 .py 檔", type=["py"], key="new_signal_uploader")
if uploaded_signal_file is not None:
    new_content = uploaded_signal_file.getvalue().decode("utf-8")
    try:
        compile(new_content, uploaded_signal_file.name, "exec")
    except SyntaxError as e:
        st.error(f"語法錯誤，未儲存：{e}")
    else:
        st.code(new_content[:2000] + ("..." if len(new_content) > 2000 else ""), language="python")
        push_new_github = st.checkbox("同時提交到 GitHub", value=False, key="push_new_signal_github")
        if st.button("💾 儲存這個新訊號檔案", key="save_new_signal_btn"):
            new_path = os.path.join(SIGNAL_DIR, uploaded_signal_file.name)
            with open(new_path, "w", encoding="utf-8") as f:
                f.write(new_content)
            _, errors = module_loader.load_default_signal_modules()
            if errors:
                st.error("儲存成功，但重新載入時發生錯誤：\n" + "\n".join(errors))
            else:
                st.success(f"已新增並載入 {uploaded_signal_file.name}！")
            if push_new_github:
                ok = upload_file_to_github(
                    new_content.encode("utf-8"),
                    f"signal_module/{uploaded_signal_file.name}",
                    f"Add signal_module/{uploaded_signal_file.name} via 訊號編輯頁面",
                )
                if ok:
                    st.success("已提交到 GitHub。")
                else:
                    st.warning("提交到 GitHub 失敗，請確認 GitHub Secrets 設定。")
            st.rerun()

st.divider()
st.caption(
    "⚠️ 注意：這個頁面對檔案的修改，只會即時生效在「目前正在執行」的伺服器上。"
    "若沒有勾選「同時提交到 GitHub」，下次重新部署 / 重啟後仍會回到 GitHub repo 裡的舊版本。"
)
