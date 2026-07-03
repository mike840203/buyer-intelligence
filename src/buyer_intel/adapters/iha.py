"""IHAAdapter:IHA 展商名錄 / 與會零售商名單。

參展報名後取得;含 Rep Group 線索,是 T0 名單的核心來源。
名錄格式多為 CSV,欄位名稱依實際檔案調整 COLUMN_MAP。
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..models import RawLead, Tier
from .base import BaseAdapter

# IHA 名錄欄位 → RawLead 欄位(依實際下載的檔案調整)
COLUMN_MAP = {
    "company": ["company", "company_name", "exhibitor", "organization"],
    "contact_name": ["contact", "contact_name", "name"],
    "title": ["title", "job_title"],
    "email": ["email", "e-mail"],
    "website": ["website", "url"],
    "city": ["city"],
    "state": ["state", "province"],
}


def _pick(row: dict, keys: list[str]) -> str | None:
    lowered = {k.lower().strip(): v for k, v in row.items()}
    for key in keys:
        if lowered.get(key):
            return lowered[key].strip() or None
    return None


class IHAAdapter(BaseAdapter):
    name = "iha"

    def fetch(self, file: str = "", tier: Tier = "T0_rep", **kwargs) -> list[RawLead]:
        path = Path(file)
        if not path.exists():
            raise FileNotFoundError(f"找不到 IHA 名錄檔案:{file}")

        leads: list[RawLead] = []
        with path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                company = _pick(row, COLUMN_MAP["company"])
                if not company:
                    continue
                leads.append(RawLead(
                    company=company,
                    contact_name=_pick(row, COLUMN_MAP["contact_name"]),
                    title=_pick(row, COLUMN_MAP["title"]),
                    email=_pick(row, COLUMN_MAP["email"]),
                    website=_pick(row, COLUMN_MAP["website"]),
                    city=_pick(row, COLUMN_MAP["city"]),
                    state=_pick(row, COLUMN_MAP["state"]),
                    tier=tier,
                    source=self.name,
                ))
        return leads
