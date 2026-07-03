"""即時 company brief:攤位上談話前 30 秒要看的一頁摘要。

輸出內容(對應架構報告 L5):這家是什麼通路、幾家店、
該談 wholesale 還是找 rep、建議報價版本(FOB / DDP)。
"""

from __future__ import annotations

from ..config import MODEL_MID
from ..llm import complete
from ..models import Lead


def company_brief(lead: Lead) -> str:
    """Sonnet 生成一頁 brief(繁體中文,攤位上自己人看的)。"""
    interactions = "\n".join(
        f"- [{i.kind}] {i.content[:200]}" for i in lead.interactions[-5:]
    ) or "(無)"
    prompt = (
        "你是 Ankomn(台灣真空保鮮罐品牌)在 The Inspired Home Show 攤位上的參謀。"
        "根據以下 lead 資料,用繁體中文寫一頁精簡 brief,分四段:\n"
        "1. 這家是誰:通路類型、規模、是否已賣競品\n"
        "2. 該怎麼談:wholesale 直供、還是該請他引介/本身就是 rep\n"
        "3. 建議報價版本:FOB(客戶自理進口)或 DDP(我方含稅到門),並說明理由\n"
        "4. 開場話術:一句依據對方背景客製的開場白(英文)\n\n"
        f"公司:{lead.company}({lead.city or ''} {lead.state or ''})\n"
        f"聯絡人:{lead.contact_name or '未知'}({lead.title or '未知'})\n"
        f"通路分層:{lead.tier} / 地區:{lead.region}\n"
        f"門市數:{lead.store_count or '未知'} / 已賣競品:{lead.sells_competitors}\n"
        f"評分:{lead.score}({lead.grade})— {lead.score_rationale or ''}\n"
        f"目前階段:{lead.stage}\n"
        f"近期互動:\n{interactions}\n"
    )
    return complete(MODEL_MID, prompt, max_tokens=1500)
