"""L2 清洗與豐富層:三個節點串行。

1. 去重正規化 — 公司名模糊比對 + email domain 合併(rapidfuzz)
2. email 驗證 — Hunter.io;無效信箱直接降低可信度
3. 背景豐富 — Sonnet + web_search 補全門市數、通路類型、是否已賣競品,
   再由 Haiku 抽取為結構化 EnrichmentFacts(兩段式:搜尋歸搜尋、抽取歸抽取,
   符合模型分級用工原則)
"""

from __future__ import annotations

import re

import httpx
from rapidfuzz import fuzz

from .config import HUNTER_API_KEY, KNOWN_COMPETITORS, MODEL_FAST, MODEL_MID
from .llm import complete, complete_structured
from .models import EnrichmentFacts, Lead, RawLead, Region

# 州別 → 戰略地區映射(對應戰略報告「目標地區優先序」)
STATE_TO_REGION: dict[str, Region] = {
    "WA": "PNW", "OR": "PNW",
    "TX": "TX",
    "CA": "CA",
    "NY": "NY", "NJ": "NY", "CT": "NY",
    "IL": "MIDWEST", "WI": "MIDWEST", "MI": "MIDWEST",
    "MN": "MIDWEST", "OH": "MIDWEST", "IN": "MIDWEST",
}


def map_region(state: str | None) -> Region:
    if not state:
        return "OTHER"
    return STATE_TO_REGION.get(state.upper().strip(), "OTHER")


def _domain(email: str | None) -> str | None:
    if email and "@" in email:
        return email.split("@", 1)[1].lower()
    return None


# 公司名正規化:去除法律後綴與標點,避免 "Foo, Inc." 與 "Foo" 被視為兩家
_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|group|holdings)\b\.?", re.IGNORECASE
)


def _normalize_company(name: str) -> str:
    name = _LEGAL_SUFFIXES.sub("", name.lower())
    return re.sub(r"[^\w\s]", " ", name).strip()


def dedupe(raw_leads: list[RawLead], threshold: int = 90) -> list[RawLead]:
    """去重:email domain 相同、或公司名模糊比對 ≥ threshold 視為同一家。

    保留資訊較完整的一筆(有 email / 聯絡人者優先)。
    """
    def richness(l: RawLead) -> int:
        return sum(bool(v) for v in (l.email, l.contact_name, l.title, l.website))

    kept: list[RawLead] = []
    for lead in sorted(raw_leads, key=richness, reverse=True):
        dup = False
        for existing in kept:
            same_domain = (
                _domain(lead.email) is not None
                and _domain(lead.email) == _domain(existing.email)
            )
            similar_name = fuzz.token_sort_ratio(
                _normalize_company(lead.company), _normalize_company(existing.company)
            ) >= threshold
            if same_domain or similar_name:
                dup = True
                break
        if not dup:
            kept.append(lead)
    return kept


def verify_email(email: str) -> bool:
    """Hunter.io email 驗證;未設金鑰時視為未驗證(不阻擋流程)。"""
    if not HUNTER_API_KEY:
        return False
    resp = httpx.get(
        "https://api.hunter.io/v2/email-verifier",
        params={"email": email, "api_key": HUNTER_API_KEY},
        timeout=30,
    )
    resp.raise_for_status()
    status = resp.json().get("data", {}).get("status", "")
    return status in ("valid", "accept_all", "webmail")


def to_lead(raw: RawLead) -> Lead:
    """RawLead → Lead:正規化 + 地區映射 + email 驗證。"""
    lead = Lead(
        company=raw.company.strip(),
        contact_name=raw.contact_name,
        title=raw.title,
        email=raw.email.lower().strip() if raw.email else None,
        website=raw.website,
        city=raw.city,
        state=raw.state,
        tier=raw.tier,
        region=map_region(raw.state),
        source=raw.source,
        enrichment_notes=raw.notes,
    )
    if lead.email:
        try:
            lead.email_verified = verify_email(lead.email)
        except httpx.HTTPError:
            lead.email_verified = False
    return lead


def research_company(lead: Lead) -> str:
    """Sonnet + web_search:蒐集公司背景的自由文字摘要。"""
    competitors = "、".join(KNOWN_COMPETITORS)
    prompt = (
        f"Research the US retailer/company '{lead.company}'"
        f"{f' ({lead.website})' if lead.website else ''}"
        f"{f' in {lead.city}, {lead.state}' if lead.state else ''}. "
        "I am a Taiwanese vacuum food-storage container brand (Ankomn) preparing "
        "B2B wholesale outreach. Find and summarize: "
        "1) number of retail locations (or online-only), "
        "2) channel type (specialty coffee gear e-commerce / coffee roaster chain / "
        "kitchenware specialty / general retail / sales rep group), "
        f"3) whether they already sell competing storage products such as {competitors}, "
        "4) rough revenue scale if public. Keep it under 200 words."
    )
    return complete(MODEL_MID, prompt, max_tokens=2048, web_search=True)


def extract_facts(summary: str) -> EnrichmentFacts:
    """Haiku:從搜尋摘要抽取結構化事實(高頻低難度 → 用便宜模型)。"""
    facts = complete_structured(
        MODEL_FAST,
        f"從以下公司背景摘要抽取結構化欄位。無法確定的欄位留空(null)。\n\n{summary}",
        EnrichmentFacts,
    )
    if facts is None:
        # 解析失敗時保底:留原始摘要,不中斷 pipeline
        facts = EnrichmentFacts(summary=summary)
    return facts


def enrich_lead(lead: Lead) -> Lead:
    """L2 完整流程:背景豐富並回填 Lead。"""
    summary = research_company(lead)
    facts = extract_facts(summary)
    lead.store_count = facts.store_count
    lead.sells_competitors = facts.sells_competitors
    lead.enrichment_notes = facts.summary
    return lead
