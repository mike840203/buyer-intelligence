"""全域設定:模型分工、評分權重、路徑。

模型分級用工(對應架構報告第 05 節,已更新為現行模型 ID):
- Haiku  → 高頻低難度:資料清洗、欄位抽取、名片 OCR 結構化
- Sonnet → 中頻推理:背景豐富、契合度判斷、信件與 brief 生成
- Opus   → 低頻高價值:信件批判審稿(扮演美國 buyer)
"""

import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── LLM 後端 ──
# claude_code(預設):透過 Claude Code CLI 呼叫,走訂閱額度,免 API key/儲值
# api:Anthropic SDK 直連,需組織有 API 額度,品質與速度最佳
LLM_BACKEND = os.getenv("LLM_BACKEND", "claude_code")
def _find_claude_cli() -> str:
    """解析 claude 執行檔,逐一驗證真的存在(斷掉的符號連結視同不存在)。

    順序:環境變數(不驗證,尊重使用者)→ PATH → 獨立版安裝位置
    → VSCode 擴充內建(擴充更新會換資料夾,取修改時間最新者)。
    """
    env = os.getenv("CLAUDE_CLI")
    if env:
        return env
    which = shutil.which("claude")
    if which and Path(which).exists():   # exists() 會解析符號連結
        return which
    local = Path.home() / ".local" / "bin" / "claude"
    if local.exists():
        return str(local)
    candidates = list(Path.home().glob(
        ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude"
    ))
    if candidates:
        return str(max(candidates, key=lambda p: p.stat().st_mtime))
    return "claude"  # 找不到:留給執行時報明確錯誤(含修復指引)


CLAUDE_CLI = _find_claude_cli()

# ── 模型分工 ──
MODEL_FAST = "claude-haiku-4-5"   # 清洗、抽取、OCR 後結構化
MODEL_MID = "claude-sonnet-5"     # 豐富、評分判斷、信件生成
MODEL_TOP = "claude-opus-4-8"     # 信件批判審稿、疑難策略建議

# claude_code 後端的模型別名映射(訂閱方案沒有 Opus 時,把 top 改成 "sonnet")
CLI_MODEL_MAP = {
    MODEL_FAST: os.getenv("CLI_MODEL_FAST", "haiku"),
    MODEL_MID: os.getenv("CLI_MODEL_MID", "sonnet"),
    MODEL_TOP: os.getenv("CLI_MODEL_TOP", "opus"),
}

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

# 註:公司身分(價值主張、寄件人、展會/campaign、競品)已移到 company/*.toml
# (見 company.py),程式碼不再寫死任何一家公司。此處只留「系統行為」的旋鈕。

# ── L6 送後引擎:寄送節奏與暖機(防 Gmail 風控;對應 exportlab warmup 規則)──
# 寄送後端:eml(預設,乾跑——輸出 .eml 由人寄,安全)| gmail(自動寄,需 OAuth)
SENDING_BACKEND = os.getenv("SENDING_BACKEND", "eml")
# 是否在信末附 CAN-SPAM 合規 footer(寄件人實體地址 + reply 退訂機制)
ENABLE_COMPLIANCE_FOOTER = os.getenv("ENABLE_COMPLIANCE_FOOTER", "1") != "0"

# 暖機:前 WARMUP_WEEKS 週每日上限壓低,之後放寬。空字串=以第一封實際寄出日起算。
WARMUP_START_DATE = os.getenv("WARMUP_START_DATE", "")   # YYYY-MM-DD
WARMUP_WEEKS = int(os.getenv("WARMUP_WEEKS", "2"))
DAILY_LIMIT_WARMUP = int(os.getenv("DAILY_LIMIT_WARMUP", "2"))
DAILY_LIMIT_NORMAL = int(os.getenv("DAILY_LIMIT_NORMAL", "5"))

# 寄信時段(buyer 當地時間)與同批錯開間隔(分鐘)
SEND_WINDOW_START = os.getenv("SEND_WINDOW_START", "09:30")
SEND_WINDOW_END = os.getenv("SEND_WINDOW_END", "16:30")
INTERVAL_MIN_MINUTES = int(os.getenv("INTERVAL_MIN_MINUTES", "60"))
INTERVAL_MAX_MINUTES = int(os.getenv("INTERVAL_MAX_MINUTES", "90"))
# 查不到 buyer 州別時的預設時區(美國東岸;商業活動最集中)
FALLBACK_TIMEZONE = os.getenv("FALLBACK_TIMEZONE", "America/New_York")

# 三輪跟進的寄送 offset(工作日):seq1=+0、seq2=+4、seq3=+6(業界共識節奏)
FOLLOWUP_OFFSETS_WORKDAYS = (0, 4, 6)
MAX_SEQUENCE = 3   # 同一收件人最多寄幾封(硬上限,超過不寄)

# 同一 domain 連續退信幾次就自動加入退訂名單(防高 bounce 傷寄件信譽)
BOUNCE_LIMIT = 3
