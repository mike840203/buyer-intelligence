"""全域設定:模型分工、評分權重、路徑。

模型分級用工(對應架構報告第 05 節,已更新為現行模型 ID):
- Haiku  → 高頻低難度:資料清洗、欄位抽取、名片 OCR 結構化
- Sonnet → 中頻推理:背景豐富、契合度判斷、信件與 brief 生成
- Opus   → 低頻高價值:信件批判審稿(扮演美國 buyer)
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── 模型分工 ──
MODEL_FAST = "claude-haiku-4-5"   # 清洗、抽取、OCR 後結構化
MODEL_MID = "claude-sonnet-5"     # 豐富、評分判斷、信件生成
MODEL_TOP = "claude-opus-4-8"     # 信件批判審稿、疑難策略建議

# ── 路徑 ──
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "leads.db"
CHECKPOINT_PATH = DATA_DIR / "checkpoints.db"
OUTBOX_DIR = ROOT / "outbox"          # 人工覆核通過的信件輸出於此,由人工寄送
DASHBOARD_PATH = ROOT / "dashboard.html"

# ── 評分權重(對應架構報告 L3 評分表)──
SCORE_WEIGHTS = {
    "channel_fit": 0.40,   # 通路契合度(LLM 判斷)
    "size_fit": 0.25,      # 規模適配度(規則)
    "region": 0.20,        # 地區優先序(規則)
    "authority": 0.15,     # 決策權(規則 + LLM)
}

# 地區權重:依戰略報告目標地區優先序 P1 > P2 > P3
REGION_SCORES = {
    "PNW": 100,      # P1 太平洋西北:精品咖啡首都
    "TX": 100,       # P1 德州:團隊駐地主場
    "CA": 80,        # P2 加州
    "NY": 80,        # P2 紐約都會區
    "MIDWEST": 60,   # P3 中西部(展會主場)
    "OTHER": 30,
}

# 規模甜蜜點:5–100 家門市;過大反而扣分(第一年接不住 Costco 級訂單)
SIZE_SWEET_MIN = 5
SIZE_SWEET_MAX = 100

# 分級門檻:≥70 → A;50–69 → B;<50 → C(歸檔不觸達)
GRADE_A_THRESHOLD = 70
GRADE_B_THRESHOLD = 50

# 信件批判迴圈上限(生成 → 批判 → 重寫,最多三輪)
MAX_CRITIQUE_ROUNDS = 3

# ── 外部服務金鑰 ──
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY", "")
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY", "")
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")

# ── 展會資訊(寫入 outreach 信件)──
TIHS_BOOTH = os.getenv("TIHS_BOOTH", "Clean + Contain 分區,攤位號碼待定")
CALENDLY_URL = os.getenv("CALENDLY_URL", "")

# 已知競品(用於 L2 豐富時判斷「該通路已有保鮮罐品類貨架」)
KNOWN_COMPETITORS = ["Fellow Atmos", "Planetary Design", "Airscape", "OXO POP"]
