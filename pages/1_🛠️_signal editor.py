"""
🛠️ 訊號編輯
=============
瀏覽 / 編輯 signal_module/ 底下的訊號規則檔案。

存檔後：
1. 立即在「本次」執行的伺服器上生效（呼叫 module_loader 重新載入訊號登記表）
2. 可選擇「同時提交到 GitHub」，讓下次部署 / 重新啟動時也不會遺失變更
   （沿用主程式已經在使用的 GITHUB_TOKEN / GITHUB_OWNER / GITHUB_REPO / GITHUB_BRANCH secrets）
"""
import base64
import os

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


# ===== 沿用主程式既有的 GitHub 上傳方式 =====
def github_repo_config():
    return {
        "token": st.secrets.get("GITHUB_TOKEN", ""),
        "owner": st.secrets.get("GITHUB_OWNER", "henglunlin"),
        "repo": st.secrets.get("GITHUB_REPO", "stock-scanner-FUBAN"),
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

    edited_content = st.text_area(
        "原始碼", value=original_content, height=480, key=f"editor_{picked_file}",
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
