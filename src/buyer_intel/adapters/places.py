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
        max_results: int = 20,
        **kwargs,
    ) -> list[RawLead]:
        if not GOOGLE_MAPS_API_KEY:
            raise RuntimeError("未設定 GOOGLE_MAPS_API_KEY(見 .env.example)")

        resp = httpx.post(
            API_URL,
            json={"textQuery": query, "maxResultCount": max_results},
            headers={
                "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
                "X-Goog-FieldMask": (
                    "places.displayName,places.formattedAddress,places.websiteUri"
                ),
            },
            timeout=30,
        )
        resp.raise_for_status()

        leads: list[RawLead] = []
        for place in resp.json().get("places", []):
            address = place.get("formattedAddress", "")
            # 從地址粗略抽出州別縮寫(例如 "... Seattle, WA 98101, USA")
            state = None
            parts = [p.strip() for p in address.split(",")]
            for part in parts:
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
                notes=f"地址:{address}(需回頭用 Apollo/Hunter 找決策人)",
            ))
        return leads
