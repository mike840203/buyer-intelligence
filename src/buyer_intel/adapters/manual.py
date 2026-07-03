"""ManualAdapter:LinkedIn Sales Navigator 匯出 / 一般 CSV 匯入。

展中名片 OCR 的入庫也走此入口(field_ops.ocr 產出 RawLead 後直接存)。
CSV 欄位:company, contact_name, title, email, website, city, state, tier
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..models import RawLead
from .base import BaseAdapter

VALID_TIERS = {"T0_rep", "T1_coffee", "T2_kitchen", "T3_mass"}


class ManualAdapter(BaseAdapter):
    name = "manual"

    def fetch(self, file: str = "", **kwargs) -> list[RawLead]:
        path = Path(file)
        if not path.exists():
            raise FileNotFoundError(f"找不到 CSV 檔案:{file}")

        leads: list[RawLead] = []
        with path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                row = {k.lower().strip(): (v or "").strip() for k, v in row.items()}
                if not row.get("company"):
                    continue
                tier = row.get("tier", "T1_coffee")
                if tier not in VALID_TIERS:
                    tier = "T1_coffee"
                leads.append(RawLead(
                    company=row["company"],
                    contact_name=row.get("contact_name") or None,
                    title=row.get("title") or None,
                    email=row.get("email") or None,
                    website=row.get("website") or None,
                    city=row.get("city") or None,
                    state=row.get("state") or None,
                    tier=tier,  # type: ignore[arg-type]
                    source=self.name,
                ))
        return leads
