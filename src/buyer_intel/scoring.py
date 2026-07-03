"""L3 評分分級層:混合式評分。

先用規則算基礎分(可解釋、零成本),再由 LLM 對「通路契合度」與「決策權」
做質性判斷,兩者加權(權重見 config.SCORE_WEIGHTS):

- 通路契合度 40%(LLM):咖啡器材通路 > 廚房專賣 > 一般零售;已賣競品加分
- 規模適配度 25%(規則):甜蜜點 5–100 家門市;過大反而扣分
- 地區優先序 20%(規則):PNW、TX > CA、NY > MIDWEST
- 決策權   15%(規則 + LLM):Owner / Buyer / Category Manager 得高分

條件邊:≥70 → A 級進 L4;50–69 → B 級批次處理;<50 → 歸檔不觸達。
Rep Group(T0)走獨立通道,不套零售商評分。
"""

from __future__ import annotations

from .config import (
    GRADE_A_THRESHOLD,
    GRADE_B_THRESHOLD,
    MODEL_MID,
    REGION_SCORES,
    SCORE_WEIGHTS,
    SIZE_SWEET_MIN,
    SIZE_SWEET_MAX,
)
from .llm import client
from .models import FitJudgment, Grade, Lead

# 職稱關鍵字 → 決策權基礎分(規則部分,與 LLM 判斷各佔一半)
AUTHORITY_KEYWORDS = {
    "owner": 100, "founder": 100, "ceo": 95, "president": 95,
    "buyer": 90, "category manager": 90, "merchandis": 85,
    "purchasing": 85, "director": 70, "manager": 55,
}


def rule_size_score(store_count: int | None) -> float:
    """規模適配度:甜蜜點 5–100 家;未知給中間值;過大扣分。"""
    if store_count is None:
        return 50.0  # 未知:不獎不罰
    if SIZE_SWEET_MIN <= store_count <= SIZE_SWEET_MAX:
        return 100.0
    if store_count < SIZE_SWEET_MIN:
        return 60.0   # 太小:仍可能是好客戶,但單量有限
    if store_count <= 500:
        return 40.0   # 偏大:履約壓力升高
    return 15.0       # Costco 級:第一年接不住


def rule_region_score(region: str) -> float:
    return float(REGION_SCORES.get(region, REGION_SCORES["OTHER"]))


def rule_authority_score(title: str | None) -> float:
    if not title:
        return 30.0
    lowered = title.lower()
    for keyword, score in AUTHORITY_KEYWORDS.items():
        if keyword in lowered:
            return float(score)
    return 30.0


def llm_fit_judgment(lead: Lead) -> FitJudgment:
    """Sonnet 質性判斷:通路契合度 + 決策權(佐以背景摘要)。"""
    response = client().messages.parse(
        model=MODEL_MID,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": (
                "你在替台灣真空保鮮罐品牌 Ankomn 評估美國 B2B 買家線索。"
                "Ankomn 的核心價值主張是咖啡豆與食品的真空保鮮,"
                "主攻通路優先序:精品咖啡器材通路 > 廚房專賣零售 > 一般零售。"
                "已販售競品保鮮罐(如 Fellow Atmos)代表品類有貨架,應加分。\n\n"
                f"公司:{lead.company}\n"
                f"聯絡人職稱:{lead.title or '未知'}\n"
                f"通路分層:{lead.tier}\n"
                f"背景摘要:{lead.enrichment_notes or '無'}\n\n"
                "請給出 channel_fit_score(通路契合度 0-100)、"
                "authority_score(此聯絡人的採購決策權 0-100)與簡短 rationale(繁體中文)。"
            ),
        }],
        output_format=FitJudgment,
    )
    judgment = response.parsed_output
    if judgment is None:
        judgment = FitJudgment(
            channel_fit_score=50, authority_score=50,
            rationale="LLM 判斷解析失敗,給予中性分數",
        )
    return judgment


def grade_of(score: float) -> Grade:
    if score >= GRADE_A_THRESHOLD:
        return "A"
    if score >= GRADE_B_THRESHOLD:
        return "B"
    return "C"


def score_lead(lead: Lead) -> Lead:
    """L3 完整流程:計分、分級、寫入可解釋 rationale。"""
    # Rep Group 走獨立通道:不套零售商評分,直接進 outreach 並標註人工評估
    if lead.tier == "T0_rep":
        lead.score = None
        lead.grade = "A"
        lead.score_rationale = "T0 Rep Group:獨立通道,不套零售商評分模型,直接進觸達並由人工評估合作條件。"
        return lead

    judgment = llm_fit_judgment(lead)

    size = rule_size_score(lead.store_count)
    region = rule_region_score(lead.region)
    # 決策權:規則與 LLM 各半,降低單一來源誤判
    authority = (rule_authority_score(lead.title) + judgment.authority_score) / 2

    score = (
        SCORE_WEIGHTS["channel_fit"] * judgment.channel_fit_score
        + SCORE_WEIGHTS["size_fit"] * size
        + SCORE_WEIGHTS["region"] * region
        + SCORE_WEIGHTS["authority"] * authority
    )
    lead.score = round(score, 1)
    lead.grade = grade_of(score)
    lead.score_rationale = (
        f"通路契合 {judgment.channel_fit_score}(40%)、規模 {size:.0f}(25%)、"
        f"地區 {region:.0f}(20%)、決策權 {authority:.0f}(15%)→ 總分 {lead.score}。"
        f"LLM 判斷:{judgment.rationale}"
    )
    if lead.grade == "C":
        lead.stage = "archived"
    return lead
