"""
動態載入訊號模組 (.py 檔案)

支援兩種來源:
1. 預設: 讀取 signal_module/ 資料夾內所有 .py 檔案 (排除 __init__.py, base.py, indicators.py, module_loader.py)
2. 使用者上傳: 讀取上傳的 .py 檔案，暫存後動態 import

每個訊號模組 .py 檔案內必須:
  from signal_module.base import SignalContext, SignalResult, register_signal
  @register_signal(key, label, description, kind="buy"/"sell")
  def fn(ctx): ...

★ 這個檔案放在 signal_module/ 套件「內」，所以：
  - DEFAULT_SIGNAL_DIR 就是這個檔案所在的資料夾本身
  - 呼叫方式固定為 from signal_module import module_loader
"""
import importlib.util
import os
import sys
import tempfile

from signal_module.base import SIGNAL_REGISTRY

DEFAULT_SIGNAL_DIR = os.path.dirname(os.path.abspath(__file__))
EXCLUDE_FILES = {"__init__.py", "base.py", "indicators.py", "module_loader.py"}


def reset_registry():
    """清空目前已註冊的訊號，避免重複載入時累積或殘留舊模組"""
    SIGNAL_REGISTRY.clear()


def load_py_file(path: str, module_name: str):
    """載入單一 .py 檔案並執行 (觸發其內的 @register_signal 裝飾器)"""
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_default_signal_modules():
    """載入 signal_module/ 資料夾內所有預設 .py 訊號檔案"""
    reset_registry()
    errors = []
    if not os.path.isdir(DEFAULT_SIGNAL_DIR):
        return SIGNAL_REGISTRY, errors

    for fname in sorted(os.listdir(DEFAULT_SIGNAL_DIR)):
        if not fname.endswith(".py") or fname in EXCLUDE_FILES:
            continue
        path = os.path.join(DEFAULT_SIGNAL_DIR, fname)
        try:
            load_py_file(path, f"signal_module.{fname[:-3]}")
        except Exception as e:
            errors.append(f"{fname}: {e}")

    return SIGNAL_REGISTRY, errors


def load_uploaded_signal_modules(uploaded_files):
    """
    載入使用者上傳的 .py 訊號檔案清單 (streamlit UploadedFile 物件的 list)
    上傳的模組會取代預設模組 (完全由使用者上傳的檔案決定要測試哪些訊號)
    """
    reset_registry()
    errors = []
    tmp_dir = tempfile.mkdtemp(prefix="signal_upload_")

    for f in uploaded_files:
        path = os.path.join(tmp_dir, f.name)
        with open(path, "wb") as out:
            out.write(f.getbuffer())
        try:
            load_py_file(path, f"uploaded_signal.{f.name[:-3]}")
        except Exception as e:
            errors.append(f"{f.name}: {e}")

    return SIGNAL_REGISTRY, errors
