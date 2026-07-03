"""ApolloAdapter:主力名單來源。

以 industry + title + 地區組合查詢,取得公司 + 聯絡人(職稱含
Buyer / Category Manager / Merchandising)與 email。
免費 tier 先驗證流程,量產期(M2)升級付費方案。
"""

from __future__ import annotations

import httpx

from ..config import APOLLO_API_KEY
from ..models import RawLead, Tier
from .base import BaseAdapter

API_URL = "https://api.apollo.io/api/v1/mixed_people/search"

# 目標職稱:有採購決策權的角色
DEFAULT_TITLES = [
    "Buyer", "Category Manager", "Merchandising Manager",
    "Purchasing Manager", "Owner", "Founder",
]


class ApolloAdapter(BaseAdapter):
    name = "apollo"

    def fetch(
        self,
        keywords: str = "specialty coffee equipment retailer",
        locations: list[str] | None = None,
        titles: list[str] | None = None,
        tier: Tier = "T1_coffee",
        per_page: int = 25,
        **kwargs,
    ) -> list[RawLead]:
        if not APOLLO_API_KEY:
            raise RuntimeError("未設定 APOLLO_API_KEY(見 .env.example)")

        payload = {
            "q_keywords": keywords,
            "person_titles": titles or DEFAULT_TITLES,
            "person_locations": locations or ["Washington, US", "Oregon, US", "Texas, US"],
            "per_page": per_page,
        }
        resp = httpx.post(
            API_URL,
            json=payload,
            headers={"X-Api-Key": APOLLO_API_KEY, "Content-Type": "application/json"},
            timeout=30,
        )
        if resp.status_code == 403:
            # 免費方案不開放 People Search API(實測 error_code: API_INACCESSIBLE)
            raise RuntimeError(
                "Apollo 免費方案不開放 People Search API,兩個選擇:\n"
                "  1. 升級付費方案(Basic 起)開通 API\n"
                "  2. 免費替代:在 Apollo 網頁介面搜尋 → 匯出 CSV →\n"
                "     buyer-intel ingest --source manual --file <匯出檔>.csv"
            )
        resp.raise_for_status()
        data = resp.json()

        leads: list[RawLead] = []
        for person in data.get("people", []):
            org = person.get("organization") or {}
            leads.append(RawLead(
                company=org.get("name") or "(未知公司)",
                contact_name=person.get("name"),
                title=person.get("title"),
                email=person.get("email"),
                website=org.get("website_url"),
                city=person.get("city"),
                state=person.get("state"),
                tier=tier,
                source=self.name,
            ))
        return leads
