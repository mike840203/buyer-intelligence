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
from .models import AltContact, EnrichmentFacts, Lead, RawLead, Region

# 州別 → 戰略地區映射(對應戰略報告「目標地區優先序」)
STATE_TO_REGION: dict[str, Region] = {
    "WA": "PNW", "OR": "PNW",
    "TX": "TX",
    "CA": "CA",
    "NY": "NY", "NJ": "NY", "CT": "NY",
    "IL": "MIDWEST", "WI": "MIDWEST", "MI": "MIDWEST",
    "MN": "MIDWEST", "OH": "MIDWEST", "IN": "MIDWEST",
}


# 州全名 → 縮寫(Apollo 匯出用全名如 "Illinois",Places 用縮寫 "IL";
# 入庫一律正規化為縮寫,UI 顯示與篩選都用州名縮寫)
_STATE_FULL_NAMES = {
    "ALABAMA": "AL", "ALASKA": "AK", "ARIZONA": "AZ", "ARKANSAS": "AR",
    "CALIFORNIA": "CA", "COLORADO": "CO", "CONNECTICUT": "CT", "DELAWARE": "DE",
    "FLORIDA": "FL", "GEORGIA": "GA", "HAWAII": "HI", "IDAHO": "ID",
    "ILLINOIS": "IL", "INDIANA": "IN", "IOWA": "IA", "KANSAS": "KS",
    "KENTUCKY": "KY", "LOUISIANA": "LA", "MAINE": "ME", "MARYLAND": "MD",
    "MASSACHUSETTS": "MA", "MICHIGAN": "MI", "MINNESOTA": "MN",
    "MISSISSIPPI": "MS", "MISSOURI": "MO", "MONTANA": "MT", "NEBRASKA": "NE",
    "NEVADA": "NV", "NEW HAMPSHIRE": "NH", "NEW JERSEY": "NJ",
    "NEW MEXICO": "NM", "NEW YORK": "NY", "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND", "OHIO": "OH", "OKLAHOMA": "OK", "OREGON": "OR",
    "PENNSYLVANIA": "PA", "RHODE ISLAND": "RI", "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD", "TENNESSEE": "TN", "TEXAS": "TX", "UTAH": "UT",
    "VERMONT": "VT", "VIRGINIA": "VA", "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV", "WISCONSIN": "WI", "WYOMING": "WY",
    "DISTRICT OF COLUMBIA": "DC",
}


def normalize_state(state: str | None) -> str | None:
    """州別正規化:全名轉縮寫、統一大寫;認不得的保留原值。"""
    if not state or not state.strip():
        return None
    code = state.upper().strip()
    if len(code) > 2:
        code = _STATE_FULL_NAMES.get(code, state.strip())
    return code


def map_region(state: str | None) -> Region:
    """州 → 戰略地區(僅供評分權重使用;UI 一律顯示州名)。"""
    code = normalize_state(state)
    if not code:
        return "OTHER"
    return STATE_TO_REGION.get(code, "OTHER")


# 免費信箱網域:不能當「同公司」證據(兩家小店都用 gmail 不代表是同一家)
FREEMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
    "aol.com", "live.com", "msn.com", "me.com", "protonmail.com", "proton.me",
}


def _domain(email: str | None) -> str | None:
    if email and "@" in email:
        return email.split("@", 1)[1].lower()
    return None


def _corporate_domain(email: str | None) -> str | None:
    """公司網域:免費信箱回 None,不參與同網域去重。"""
    domain = _domain(email)
    return None if domain in FREEMAIL_DOMAINS else domain


# 公司名正規化:去除法律後綴與標點,避免 "Foo, Inc." 與 "Foo" 被視為兩家
_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|group|holdings)\b\.?", re.IGNORECASE
)


def _normalize_company(name: str) -> str:
    name = _LEGAL_SUFFIXES.sub("", name.lower())
    return re.sub(r"[^\w\s]", " ", name).strip()


# 品類對口關鍵字:職稱帶這些字的買手,就是 Ankomn 該敲的門
CATEGORY_TITLE_KEYWORDS = [
    "hardgood", "housewares", "home", "kitchen", "coffee",
    "seasonal", "decor", "tabletop", "gourmet",
]
BUYER_TITLE_KEYWORDS = ["buyer", "category", "purchas", "merchandis"]
OWNER_TITLE_KEYWORDS = ["owner", "founder", "ceo", "president"]


def _contact_priority(l: RawLead) -> tuple[int, int]:
    """同公司多聯絡人時的保留優先序。

    品類對口 Buyer(4)> Owner/Founder(3)> 泛 Buyer(2)> 有職稱(1)> 無(0);
    同級以欄位完整度決勝。邏輯:公司大到有品類買手,門就是買手;
    小店沒有買手,Owner 自然勝出。
    """
    title = (l.title or "").lower()
    is_buyer = any(k in title for k in BUYER_TITLE_KEYWORDS)
    if is_buyer and any(k in title for k in CATEGORY_TITLE_KEYWORDS):
        rank = 4
    elif any(k in title for k in OWNER_TITLE_KEYWORDS):
        rank = 3
    elif is_buyer:
        rank = 2
    elif title:
        rank = 1
    else:
        rank = 0
    richness = sum(bool(v) for v in (l.email, l.contact_name, l.title, l.website))
    return (rank, richness)


def dedupe(
    raw_leads: list[RawLead], threshold: int = 90, verbose: bool = False
) -> list[RawLead]:
    """去重:公司 email 網域相同、或公司名模糊比對 ≥ threshold 視為同一家。

    - 一家公司只留一筆:依 _contact_priority 挑最該敲門的聯絡人
    - 被移除的同公司聯絡人存進保留者的 notes 作為「備援聯絡人」
    - 免費信箱(gmail 等)不當「同公司」證據,避免錯殺不同的小店
    - verbose=True 時逐筆印出被移除者與原因,供人工檢查
    """
    kept: list[RawLead] = []
    for lead in sorted(raw_leads, key=_contact_priority, reverse=True):
        reason = None
        matched: RawLead | None = None
        for existing in kept:
            same_domain = (
                _corporate_domain(lead.email) is not None
                and _corporate_domain(lead.email) == _corporate_domain(existing.email)
            )
            similar_name = fuzz.token_sort_ratio(
                _normalize_company(lead.company), _normalize_company(existing.company)
            ) >= threshold
            if same_domain or similar_name:
                reason = (
                    f"同公司已保留 {existing.contact_name or '無聯絡人'}"
                    f"({existing.title or '無職稱'})"
                    f"——判定依據:{'email 網域相同' if same_domain else '公司名相同'}"
                )
                matched = existing
                break
        if matched is None:
            kept.append(lead)
            continue
        # 同公司的其他聯絡人:結構化保留(不丟棄),UI 上可一鍵切換為主要收件人
        matched.alt_contacts.append(AltContact(
            contact_name=lead.contact_name, title=lead.title, email=lead.email,
        ))
        matched.alt_contacts.extend(lead.alt_contacts)  # 傳遞其自身的備援
        if verbose:
            print(f"  ✂ 併入備選「{lead.company}」({lead.contact_name or '無聯絡人'}"
                  f",{lead.title or '無職稱'})—— {reason}(全部保留,UI 可切換收件人)")
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
        state=normalize_state(raw.state),
        tier=raw.tier,
        region=map_region(raw.state),
        source=raw.source,
        enrichment_notes=raw.notes,
        alt_contacts=raw.alt_contacts,
    )
    # email 驗證不在匯入時做(同步打 API 會讓匯入卡住),移到 pipeline L2 背景執行
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
    """L2 完整流程:email 驗證 + 背景豐富,回填 Lead。

    (驗證放這裡而非匯入時:pipeline 本來就是背景長工作,多 1 秒無感;
    匯入則保持秒進不卡 UI)
    """
    if lead.email and not lead.email_verified:
        try:
            lead.email_verified = verify_email(lead.email)
        except httpx.HTTPError:
            lead.email_verified = False
    summary = research_company(lead)
    facts = extract_facts(summary)
    lead.store_count = facts.store_count
    lead.sells_competitors = facts.sells_competitors
    lead.enrichment_notes = facts.summary
    return lead
