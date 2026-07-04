"""ManualAdapter:CSV 匯入 —— 支援兩種格式,自動識別。

1. 簡易格式(examples/seed_leads.csv):
   company, contact_name, title, email, website, city, state, tier
2. Apollo 網頁匯出格式:First Name / Last Name / Title / Company / Email /
   Website / City / State / Company City / … 欄位自動映射,姓名自動合併,
   Industry / # Employees / Email Status 收進 notes 供 L2/L3 參考。

LinkedIn Sales Navigator 匯出與展中名片 OCR 也走此入口。
"""

from __future__ import annotations

import csv
from pathlib import Path

from ..models import RawLead, Tier
from .base import BaseAdapter

VALID_TIERS = {"T0_rep", "T1_coffee", "T2_kitchen", "T3_mass"}

# 欄位別名:依序取第一個有值的欄位(個人欄位優先於公司欄位)
ALIASES: dict[str, list[str]] = {
    "company": ["company", "company name", "organization", "company name for emails"],
    "contact_name": ["contact_name", "full name", "name", "contact"],
    "first_name": ["first name", "first_name"],
    "last_name": ["last name", "last_name"],
    "title": ["title", "job title"],
    "email": ["email", "work email"],
    "website": ["website", "company website", "url"],
    "city": ["city", "company city"],
    "state": ["state", "company state"],
    "tier": ["tier"],
    # 以下收進 notes,供評分與寫信參考
    "industry": ["industry"],
    "employees": ["# employees", "employees", "number of employees"],
    "email_status": ["email status"],
}


def _pick(row: dict[str, str], field: str) -> str | None:
    for alias in ALIASES[field]:
        value = row.get(alias)
        if value:
            return value
    return None


class ManualAdapter(BaseAdapter):
    name = "manual"

    def fetch(self, file: str = "", tier: Tier = "T1_coffee", **kwargs) -> list[RawLead]:
        path = Path(file)
        if not path.exists():
            raise FileNotFoundError(f"找不到 CSV 檔案:{file}")

        leads: list[RawLead] = []
        with path.open(newline="", encoding="utf-8-sig") as f:
            for raw_row in csv.DictReader(f):
                row = {
                    (k or "").lower().strip(): (v or "").strip()
                    for k, v in raw_row.items()
                }
                company = _pick(row, "company")
                if not company:
                    continue

                # 姓名:直接欄位優先,否則合併 Apollo 的 First/Last Name
                contact = _pick(row, "contact_name")
                if not contact:
                    first, last = _pick(row, "first_name"), _pick(row, "last_name")
                    contact = " ".join(p for p in (first, last) if p) or None

                # tier:CSV 欄位優先,否則用指令參數(--tier)
                row_tier = _pick(row, "tier") or tier
                if row_tier not in VALID_TIERS:
                    row_tier = tier

                # Apollo 附加資訊收進 notes
                extras = []
                for field, label in (
                    ("industry", "產業"),
                    ("employees", "員工數"),
                    ("email_status", "Apollo email 狀態"),
                ):
                    value = _pick(row, field)
                    if value:
                        extras.append(f"{label}:{value}")

                leads.append(RawLead(
                    company=company,
                    contact_name=contact,
                    title=_pick(row, "title"),
                    email=_pick(row, "email"),
                    website=_pick(row, "website"),
                    city=_pick(row, "city"),
                    state=_pick(row, "state"),
                    tier=row_tier,  # type: ignore[arg-type]
                    source=self.name,
                    notes=";".join(extras) or None,
                ))
        return leads
