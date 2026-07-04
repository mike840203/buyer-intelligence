"""核心資料模型(Pydantic)。

對應架構報告第 03 節的 Lead schema,外加 pipeline 各層的中間產物。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Tier = Literal["T0_rep", "T1_coffee", "T2_kitchen", "T3_mass"]
Region = Literal["PNW", "TX", "CA", "NY", "MIDWEST", "OTHER"]
Stage = Literal[
    "new", "contacted", "meeting_booked", "met_at_show",
    "followed_up", "sample_sent", "quoting", "po_received", "archived",
]
Grade = Literal["A", "B", "C"]


class AltContact(BaseModel):
    """同公司的其他聯絡人:去重時不丟棄,UI 上可一鍵切換為主要收件人。"""

    contact_name: str | None = None
    title: str | None = None
    email: str | None = None


class RawLead(BaseModel):
    """L1 各 adapter 的統一輸出格式。"""

    company: str
    contact_name: str | None = None
    title: str | None = None
    email: str | None = None
    website: str | None = None
    city: str | None = None
    state: str | None = None       # 美國州別縮寫,L2 據此映射 region
    tier: Tier = "T1_coffee"
    source: str = "manual"         # apollo / places / iha / manual / ocr
    notes: str | None = None
    alt_contacts: list[AltContact] = Field(default_factory=list)


class Interaction(BaseModel):
    """互動紀錄:信件、會談、樣品寄送等,全文入庫供 follow-up 生成取用。"""

    kind: Literal["email_draft", "email_sent", "meeting_note", "follow_up", "other"]
    content: str
    created_at: datetime = Field(default_factory=datetime.now)


class Lead(BaseModel):
    """一筆買家線索的完整狀態,貫穿 L1–L5。"""

    id: int | None = None  # SQLite rowid,入庫後回填

    # ── 身分 ──
    company: str
    contact_name: str | None = None
    title: str | None = None
    email: str | None = None
    email_verified: bool = False
    website: str | None = None
    city: str | None = None
    state: str | None = None
    source: str = "manual"

    # ── 分類 ──
    tier: Tier = "T1_coffee"
    region: Region = "OTHER"
    store_count: int | None = None
    sells_competitors: bool | None = None   # 已賣 Fellow Atmos 等競品 → 品類有貨架
    enrichment_notes: str | None = None     # L2 web 搜尋補全的背景摘要
    alt_contacts: list[AltContact] = Field(default_factory=list)  # 同公司其他聯絡人

    # ── 評分 ──
    score: float | None = None              # 0–100
    grade: Grade | None = None
    score_rationale: str | None = None      # 可解釋性,人工覆核用

    # ── Pipeline ──
    stage: Stage = "new"
    interactions: list[Interaction] = Field(default_factory=list)
    next_action_due: date | None = None     # 逾期警示依據
    pending_draft: str | None = None        # 待人工覆核的信件草稿

    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


# ── LLM 結構化輸出用的中間模型 ──

class CardContact(BaseModel):
    """名片 OCR 抽取結果(L5)。"""

    company: str
    contact_name: str | None = None
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None
    city: str | None = None
    state: str | None = None


class EnrichmentFacts(BaseModel):
    """L2 背景豐富的結構化事實(由 Haiku 從搜尋摘要抽取)。"""

    store_count: int | None = None
    sells_competitors: bool | None = None
    channel_type: str | None = None        # 咖啡器材電商 / 精品烘豆商 / 廚房專賣 / 一般零售…
    revenue_band: str | None = None        # 例如 "<$1M" / "$1M-$10M" / ">$10M"
    summary: str                           # 一段背景摘要,供評分與信件生成引用


class FitJudgment(BaseModel):
    """L3 LLM 質性判斷:通路契合度與決策權。"""

    channel_fit_score: int = Field(ge=0, le=100)
    authority_score: int = Field(ge=0, le=100)
    rationale: str


class CritiqueResult(BaseModel):
    """L4 Opus 批判審稿結果。"""

    verdict: Literal["pass", "revise"]
    issues: list[str] = Field(default_factory=list)
    rewrite_hints: str | None = None
