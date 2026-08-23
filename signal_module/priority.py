"""
訊號優先等級設定
==================
「買賣訊號」欄位每一檔股票同一天可能同時命中好幾個訊號，這裡決定哪些訊號比較重要、
該優先顯示/推播。數字越小越重要 (1 > 2 > 3)；同一天如果同等級的訊號一起觸發，
就一起顯示；等級不同時只顯示等級數字最小(最重要)的那些。
不在下面清單內的訊號，預設視為最低優先等級 (見 SIGNAL_PRIORITY_DEFAULT)。

改這個檔案有兩種方式：
1. 直接在下面改 PRIORITY_XXX 的數字。
2. 用「🛠️ 訊號編輯」頁面打開 priority.py，展開「🎛️ 參數面板」，用滑桿/數字輸入框調整
   （面板只認得「模組層級、全大寫命名、單純數字」的 `NAME = 數字` 這種寫法，
   所以優先等級被拆成一個個獨立的 PRIORITY_XXX 常數，而不是寫成單一個 dict 字面值 ——
   dict 字面值面板沒辦法個別編輯）。
存檔後兩種方式效果一樣：立即在「本次」執行的伺服器上生效，需要的話也可以勾選
「同時提交到 GitHub」讓下次部署/重啟不會遺失變更。

⚠️ 如果要新增一個新的訊號 label 到這個優先等級表：
在下面新增一行 `PRIORITY_XXX = 數字`，並在最下面的 _LABEL_TO_PRIORITY 裡加一行
`"訊號的 label 文字": PRIORITY_XXX,`。兩步都要做，缺一步不會生效。
"""
from signal_module import base as _base

# ── 第一級：最重要，優先顯示/推播 ─────────────────────────────
PRIORITY_BBAND_BREAKOUT = 1              # 布林縮窄突破
PRIORITY_ISLAND_REVERSAL_BEARISH = 1     # 反向島狀
PRIORITY_TRENDLINE_BREAKOUT = 1          # 下降趨勢線突破
PRIORITY_TARGET_PRICE_BUY_ZONE = 1       # 進入買入區間
PRIORITY_TARGET_PRICE_STOP_LOSS = 1      # 觸及停損價格

# ── 第二級 ───────────────────────────────────────────────
PRIORITY_THREE_K_REVERSAL = 2            # 3K反轉
PRIORITY_CLEVER_POINT = 2                # 巧妙點
PRIORITY_DOUBLE_GAP = 2                  # 雙跳空
PRIORITY_DOUBLE_LIMIT_UP = 2             # 雙漲停
PRIORITY_ISLAND_REVERSAL = 2             # 島狀反轉
PRIORITY_KD_GAO_JIAO = 2                 # KD高腳
PRIORITY_LIMIT_DOWN = 2                  # 跌停
PRIORITY_SINGLE_GAP = 2                  # 單跳空
PRIORITY_WEEKLY_1K = 2                   # 周1K

# ── 第三級：預設等級 ─────────────────────────────────────────
PRIORITY_GENERALIZED_FALLING_THREE = 3   # 廣義下降三法
PRIORITY_LIMIT_UP = 3                    # 漲停
PRIORITY_MOVING_TAKE_PROFIT = 3          # 移動停利
PRIORITY_GENERALIZED_RISING_THREE = 3    # 廣義上升三法
PRIORITY_THREE_WHITE_SOLDIERS = 3        # 三白兵

# 不在下面 _LABEL_TO_PRIORITY 清單內的訊號 (例如未來新增、還沒設定優先等級的訊號)，
# 預設視為這個等級。
SIGNAL_PRIORITY_DEFAULT = 3

# key 對應到 signal_module 各檔案 register_signal() 裡的 label。
_LABEL_TO_PRIORITY = {
    "布林縮窄突破": PRIORITY_BBAND_BREAKOUT,
    "反向島狀": PRIORITY_ISLAND_REVERSAL_BEARISH,
    "下降趨勢線突破": PRIORITY_TRENDLINE_BREAKOUT,
    "進入買入區間": PRIORITY_TARGET_PRICE_BUY_ZONE,
    "觸及停損價格": PRIORITY_TARGET_PRICE_STOP_LOSS,
    "3K反轉": PRIORITY_THREE_K_REVERSAL,
    "巧妙點": PRIORITY_CLEVER_POINT,
    "雙跳空": PRIORITY_DOUBLE_GAP,
    "雙漲停": PRIORITY_DOUBLE_LIMIT_UP,
    "島狀反轉": PRIORITY_ISLAND_REVERSAL,
    "KD高腳": PRIORITY_KD_GAO_JIAO,
    "跌停": PRIORITY_LIMIT_DOWN,
    "單跳空": PRIORITY_SINGLE_GAP,
    "周1K": PRIORITY_WEEKLY_1K,
    "廣義下降三法": PRIORITY_GENERALIZED_FALLING_THREE,
    "漲停": PRIORITY_LIMIT_UP,
    "移動停利": PRIORITY_MOVING_TAKE_PROFIT,
    "廣義上升三法": PRIORITY_GENERALIZED_RISING_THREE,
    "三白兵": PRIORITY_THREE_WHITE_SOLDIERS,
}

# 把上面的常數同步進 signal_module/base.py 的 SIGNAL_PRIORITY（這是全程只會存在一份、
# 永遠不會被砍掉重造的 dict 物件，這裡只做 .clear() + .update() 原地更新，不是重新賦值
# 一個新的 dict —— 這樣「🛠️ 訊號編輯」頁面每次存檔重新載入這個檔案之後，app.py 才能
# 正確讀到最新的優先等級設定，細節說明見 base.py 裡 SIGNAL_PRIORITY 宣告處的註解）。
_base.SIGNAL_PRIORITY.clear()
_base.SIGNAL_PRIORITY.update(_LABEL_TO_PRIORITY)
_base.SIGNAL_PRIORITY_DEFAULT = SIGNAL_PRIORITY_DEFAULT
