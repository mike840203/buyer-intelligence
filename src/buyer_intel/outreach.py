"""L4 觸達引擎:生成 → 自我批判 → 重寫 的迴圈。

- Sonnet 依 buyer 公司背景寫個人化邀約信
- Opus 扮演「收信的美國 buyer」批判這封信(太長?太罐頭?價值主張不清?)
- 不及格退回重寫,最多三輪;出迴圈後進人工覆核佇列(pending_draft)

Human-in-the-loop(架構報告設計原則 2):
發信永遠由人最終覆核 —— 本模組只產草稿,絕不直接寄送。
信中固定包含:一句話價值主張、為何找上「這一家」的具體理由、
TIHS 攤位資訊 + Calendly 預約連結。
"""

from __future__ import annotations

from .config import CALENDLY_URL, MODEL_MID, MODEL_TOP, TIHS_BOOTH
from .llm import complete, complete_structured
from .models import CritiqueResult, Interaction, Lead

VALUE_PROP = (
    "Ankomn makes patented crank-vacuum food storage containers (designed and made "
    "in Taiwan) that keep coffee beans and dry goods fresh 3-5x longer than "
    "one-way-valve canisters - no pumps, no batteries, a twist of the lid pulls a vacuum."
)


def draft_email(lead: Lead, hints: str | None = None) -> str:
    """Sonnet 生成個人化邀約信(英文,寄給美國 buyer)。"""
    rep_angle = lead.tier == "T0_rep"
    prompt = (
        "Write a cold outreach email in English for a trade-show meeting request.\n\n"
        f"Sender: Ankomn, a Taiwanese vacuum food-storage container brand.\n"
        f"Value proposition: {VALUE_PROP}\n"
        f"Trade show: The Inspired Home Show 2027, Chicago, March 9-11, {TIHS_BOOTH}.\n"
        f"Booking link: {CALENDLY_URL or '(Calendly link TBD)'}\n\n"
        f"Recipient company: {lead.company}\n"
        f"Recipient: {lead.contact_name or 'the buyer'} ({lead.title or 'title unknown'})\n"
        f"What we know about them: {lead.enrichment_notes or 'nothing specific - keep it honest, do not fabricate'}\n\n"
        + (
            "This recipient is an independent SALES REP GROUP, not a retailer: pitch a "
            "line-representation opportunity (commission-based, US retail relationships) "
            "instead of a wholesale purchase.\n"
            if rep_angle else
            "Pitch a wholesale/stocking conversation.\n"
        )
        + "Hard requirements:\n"
        "- Under 150 words, plain text, no bullet lists\n"
        "- One-sentence value proposition\n"
        "- A SPECIFIC reason why we reached out to THIS company (use what we know; "
        "never invent facts)\n"
        "- Mention the TIHS booth and offer the booking link\n"
        "- No hype words, no emoji, no generic flattery\n"
        "- End with a simple sign-off from 'The Ankomn Team'\n"
        + (f"\nReviewer feedback to address in this revision:\n{hints}\n" if hints else "")
        + "\nReturn ONLY the email body (with a subject line on the first line as "
        "'Subject: ...')."
    )
    return complete(MODEL_MID, prompt, max_tokens=1024)


def critique_email(draft: str, lead: Lead) -> CritiqueResult:
    """Opus 扮演美國 buyer 批判信件。一封爛信毀掉一個 A 級 lead,值得用最強模型把關。"""
    result = complete_structured(
        MODEL_TOP,
        (
            f"You are a busy US retail buyer at {lead.company} "
            f"({lead.title or 'buyer'}). You get dozens of cold vendor emails a day "
            "and delete anything generic. Critique this trade-show outreach email "
            "ruthlessly:\n\n"
            f"---\n{draft}\n---\n\n"
            "Fail it (verdict='revise') if ANY of these are true: over 150 words; "
            "reads like a template; the 'why you specifically' reason is vague or "
            "fabricated; value proposition unclear; pushy or hype-y tone; missing "
            "booth info or booking link. Otherwise verdict='pass'. "
            "List concrete issues and give rewrite_hints if revising."
        ),
        CritiqueResult,
    )
    if result is None:
        # 解析失敗:保守處理,交人工覆核而非退回重寫
        result = CritiqueResult(verdict="pass", issues=["批判結果解析失敗,請人工加強檢查"])
    return result


def draft_follow_up(lead: Lead) -> str:
    """展中每晚批次:依當日會談紀錄生成 same-day follow-up 草稿(仍需人工掃過)。"""
    notes = "\n".join(
        f"- {i.content}" for i in lead.interactions if i.kind == "meeting_note"
    ) or "(no meeting notes recorded)"
    prompt = (
        "Write a same-day follow-up email in English after meeting this buyer at "
        "The Inspired Home Show booth today.\n\n"
        f"Company: {lead.company}\n"
        f"Contact: {lead.contact_name or 'the buyer'} ({lead.title or ''})\n"
        f"Meeting notes from today:\n{notes}\n\n"
        "Requirements: under 120 words; reference something SPECIFIC from the meeting "
        "notes (if notes are empty, keep it short and thank them for stopping by); "
        "state the concrete next step (samples / quote / call); no hype. "
        "Return only the email with 'Subject: ...' on the first line."
    )
    return complete(MODEL_MID, prompt, max_tokens=1024)


def queue_for_review(lead: Lead, draft: str, critique: CritiqueResult) -> Lead:
    """出迴圈後進人工覆核佇列:草稿掛上 lead,等 CLI `review` 指令處理。"""
    lead.pending_draft = draft
    note = "通過 Opus 審稿" if not critique.issues else "審稿備註:" + ";".join(critique.issues)
    lead.interactions.append(Interaction(
        kind="email_draft",
        content=f"{draft}\n\n[{note}]",
    ))
    return lead
