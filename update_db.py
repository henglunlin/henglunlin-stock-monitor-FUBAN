import pandas as pd
import requests
import sqlite3
import time
import urllib3
import os
from datetime import datetime, timezone, timedelta
from typing import Any

import benchmark_utils

# 本機執行時從專案根目錄的 .env 讀取 TELEGRAM_BOT_TOKEN 等環境變數
# （在 GitHub Actions 上這些值是由 secrets 直接注入，沒有 .env 檔，這段會自動略過）。
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TWSE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_URL = "https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json, text/plain, */*"}

# 執行環境標記：GitHub Actions 會自動設 GITHUB_ACTIONS=true，其餘（GMK 排程）視為 LHL 伺服器。
# Telegram 訊息用它來區分是哪一邊跑的，避免兩邊同時在跑時分不出來。
IS_GITHUB_ACTIONS = os.environ.get("GITHUB_ACTIONS", "").lower() == "true"
RUN_TAG = "【GitHub Actions】" if IS_GITHUB_ACTIONS else "【LHL 伺服器】"
RUN_ICON = "☁️" if IS_GITHUB_ACTIONS else "🖥️"
DB_NAME = "twse_ohlcv.db"

# ======== 新增 Telegram 推播函數 ========
def send_telegram_message(text: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("未設定 Telegram 變數，略過推播。")
        return
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram 傳送失敗: {e}")
# ========================================

def number(value: Any) -> float:
    text = str(value).replace(",", "").strip()
    if text in {"", "-", "--", "---", "----", "None", "nan", "X"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0

def get_json(url: str, params: dict[str, str] | None = None) -> Any:
    response = requests.get(url, params=params, headers=HEADERS, timeout=30, verify=False)
    response.raise_for_status()
    return response.json()

def check_status(payload: dict[str, Any], source: str) -> bool:
    status = str(payload.get("stat", ""))
    if status in {"", "OK"}: return True
    if "沒有符合條件的資料" in status: return False
    raise RuntimeError(f"{source} status: {status}")

def unique_columns(fields: list[Any]) -> list[str]:
    seen, result = {}, []
    for field in fields:
        base = str(field).strip()
        seen[base] = seen.get(base, 0) + 1
        result.append(base if seen[base] == 1 else f"{base}_{seen[base]}")
    return result

def fetch_twse_daily(report_date: str, return_payload: bool = False):
    """
    return_payload=True 時改回傳 (df, payload) tuple——2026-08-17 新增：
    讓呼叫端可以把這裡已經打過一次的官方 MI_INDEX payload 直接轉給
    benchmark_utils.update_benchmark_daily() 解析大盤指數，不用為了大盤
    又對同一個官方端點多打一次一模一樣的請求，降低被限流(429)的風險。
    """
    payload = None
    try:
        payload = get_json(TWSE_URL, {"date": report_date, "type": "ALLBUT0999", "response": "json"})
        if not check_status(payload, "prices"):
            return (pd.DataFrame(), payload) if return_payload else pd.DataFrame()

        for table in payload.get("tables", []):
            columns = unique_columns(table.get("fields", []))
            if "證券代號" in columns and "收盤價" in columns:
                df = pd.DataFrame(table.get("data", []), columns=columns)
                target_cols = ["證券代號", "證券名稱", "開盤價", "最高價", "最低價", "收盤價", "成交股數"]
                df = df[target_cols].copy()
                df.columns = ["SecurityCode", "SecurityName", "Open", "High", "Low", "Close", "Volume"]
                df["SecurityCode"] = df["SecurityCode"].astype(str).str.strip()
                df["SecurityName"] = df["SecurityName"].astype(str).str.strip()
                for col in ["Open", "High", "Low", "Close", "Volume"]:
                    df[col] = df[col].map(number)
                df.insert(0, "Date", pd.to_datetime(report_date, format="%Y%m%d").date())
                df.insert(1, "Market", "上市")
                result_df = df[df["Close"] > 0].drop_duplicates("SecurityCode")
                return (result_df, payload) if return_payload else result_df
        return (pd.DataFrame(), payload) if return_payload else pd.DataFrame()
    except Exception as e:
        print(f"上市資料解析失敗 ({report_date}): {e}")
        return (pd.DataFrame(), payload) if return_payload else pd.DataFrame()

def fetch_tpex_daily(report_date: str) -> pd.DataFrame:
    dt = datetime.strptime(report_date, "%Y%m%d")
    roc_date = f"{dt.year - 1911}/{dt.strftime('%m/%d')}"
    try:
        payload = get_json(TPEX_URL, {"l": "zh-tw", "d": roc_date, "se": "EW", "o": "json"})
        data_list = []
        if "tables" in payload and payload["tables"]:
            for table in payload["tables"]: data_list.extend(table.get("data", []))
        elif "aaData" in payload: data_list = payload["aaData"]
        
        if not data_list: return pd.DataFrame()
        
        rows = []
        for row in data_list:
            if len(row) >= 8:
                code = str(row[0]).strip()
                if len(code) > 6: continue
                close_p = number(row[2])
                if close_p > 0:
                    rows.append({
                        "Date": dt.date(), "Market": "上櫃", "SecurityCode": code, "SecurityName": str(row[1]).strip(),
                        "Open": number(row[4]), "High": number(row[5]), "Low": number(row[6]), "Close": close_p, "Volume": number(row[7])
                    })
        df = pd.DataFrame(rows)
        return df.drop_duplicates("SecurityCode") if not df.empty else df
    except Exception as e:
        print(f"上櫃資料解析失敗 ({report_date}): {e}")
        return pd.DataFrame()

def save_to_database(df: pd.DataFrame):
    if df.empty: return
    with sqlite3.connect(DB_NAME) as conn:
        dates = df["Date"].unique()
        markets = df["Market"].unique()
        for d in dates:
            for m in markets:
                conn.execute("DELETE FROM ohlcv_data WHERE Date = ? AND Market = ?", (str(d), m))
        df.to_sql("ohlcv_data", conn, if_exists="append", index=False)

if __name__ == "__main__":
    # 設定台灣時區
    tw_tz = timezone(timedelta(hours=8))
    tw_now = datetime.now(tw_tz)
    
    # 建立要抓取的日期清單 (昨天與今天)
    dates_to_fetch = [
        (tw_now - timedelta(days=1)).strftime("%Y%m%d"),
        tw_now.strftime("%Y%m%d")
    ]
    
    summary_lines = []
    has_valid_data = False

    # 2026-08-16 新增：首次部署這個功能時，資料庫裡還沒有任何大盤歷史資料 (連 MA60 都不夠算)，
    # 這裡在正式跑每日更新之前先檢查一次，不足的話用 yfinance 一次補齊 2 年份，
    # 之後每次執行只會看到「資料已足夠、不用補」直接跳過，成本很低。
    try:
        backfilled = benchmark_utils.ensure_benchmark_history(DB_NAME)
        if backfilled:
            print("📈 大盤指數歷史資料已補齊/更新（首次執行或資料過舊）。")
    except Exception as e:
        print(f"⚠️ 大盤指數歷史資料檢查失敗，略過不擋主流程：{e}")

    for date_str in dates_to_fetch:
        print(f"開始執行自動抓取: {date_str}")
        # 2026-08-17 修改：改用 return_payload=True 拿回原始 MI_INDEX payload，
        # 下面同步大盤指數時可以直接複用，不用再多打一次官方 API。
        twse_df, mi_payload = fetch_twse_daily(date_str, return_payload=True)
        time.sleep(2) # 稍微等待，避免請求過於頻繁
        tpex_df = fetch_tpex_daily(date_str)

        daily_df = pd.concat([twse_df, tpex_df], ignore_index=True)
        if not daily_df.empty:
            save_to_database(daily_df)
            msg = f"📅 {date_str}: 上市 {len(twse_df)} 檔 / 上櫃 {len(tpex_df)} 檔"
            print(f"✅ {msg}")
            summary_lines.append(msg)
            has_valid_data = True
        else:
            msg = f"⏸️ {date_str}: 無交易資料 (可能為假日)"
            print(msg)
            summary_lines.append(msg)

        # 2026-08-16 新增：同步更新大盤（加權指數）當天收盤值，供掃描器算「個股 vs 大盤」比較欄位。
        # 官方 MI_INDEX 端點抓不到時 (欄位異動/該日休市) 會自動 fallback 用 yfinance 補值，
        # 失敗也不影響上面個股資料已經寫入成功的結果，只印一行訊息。
        try:
            bench_ok = benchmark_utils.update_benchmark_daily(DB_NAME, date_str, mi_index_payload=mi_payload)
            print(f"{'✅' if bench_ok else '⏸️'} {date_str}: 大盤指數{'已更新' if bench_ok else '無新資料/更新失敗'}")
        except Exception as e:
            print(f"⚠️ {date_str}: 大盤指數更新發生例外，略過不擋主流程：{e}")

        time.sleep(2) # 迴圈之間保護性暫停

    # 如果這兩天之中至少有一天有資料，就發送 Telegram 推播
    if has_valid_data:
        success_msg = (
            f"{RUN_ICON} <b>{RUN_TAG}資料庫更新成功</b>\n" +
            "\n".join(summary_lines) +
            ("\n🤖 GitHub Actions 已將資料推回 Repo。" if IS_GITHUB_ACTIONS
             else "\n🏠 已寫入 LHL 伺服器本機 twse_ohlcv.db。")
        )
        send_telegram_message(success_msg)
    else:
        print("兩日皆無交易資料，不發送推播。")
