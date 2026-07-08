"""PlacesAdapter:掃描 P1/P2 城市的精品咖啡店與廚房用品獨立零售商。

只能拿到店名與地址;拿到店名後回頭用 Apollo / Hunter 找決策人
(此為架構報告 L1 表格明訂的流程)。
使用 Places API (New) 的 Text Search。
"""

from __future__ import annotations

import httpx

from ..config import GOOGLE_MAPS_API_KEY
from ..models import RawLead, Tier
from .base import BaseAdapter

API_URL = "https://places.googleapis.com/v1/places:searchText"


class PlacesAdapter(BaseAdapter):
    name = "places"

    def fetch(
        self,
        query: str = "specialty coffee roaster in Seattle, WA",
        tier: Tier = "T1_coffee",
        max_results: int = 60,   # 跨頁上限;Google 單一查詢通常最多給 ~60 家
        **kwargs,
    ) -> list[RawLead]:
        if not GOOGLE_MAPS_API_KEY:
            raise RuntimeError("未設定 GOOGLE_MAPS_API_KEY(見 .env.example)")

        leads: list[RawLead] = []
        page_token: str | None = None
        # Places Text Search 單頁上限 20 筆,靠 nextPageToken 翻頁湊到 max_results
        while len(leads) < max_results:
            body: dict = {"textQuery": query, "maxResultCount": 20}
            if page_token:
                body["pageToken"] = page_token
            resp = httpx.post(
                API_URL,
                json=body,
                headers={
                    "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
                    "X-Goog-FieldMask": (
                        "places.displayName,places.formattedAddress,"
                        "places.websiteUri,nextPageToken"
                    ),
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            for place in data.get("places", []):
                address = place.get("formattedAddress", "")
                # 從地址粗略抽出州別縮寫(例如 "... Seattle, WA 98101, USA")
                state = None
                for part in (p.strip() for p in address.split(",")):
                    token = part.split(" ")[0]
                    if len(token) == 2 and token.isalpha() and token.isupper():
                        state = token
                        break
                leads.append(RawLead(
                    company=place.get("displayName", {}).get("text", "(未知店家)"),
                    website=place.get("websiteUri"),
                    state=state,
                    tier=tier,
                    source=self.name,
                    notes=f"地址:{address}(需回頭用 Hunter 網域反查找決策人)",
                ))

            page_token = data.get("nextPageToken")
            if not page_token:
                break  # 沒有下一頁了

        return leads[:max_results]
