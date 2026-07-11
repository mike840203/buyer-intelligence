"""L4 觸達引擎:生成 → 自我批判 → 重寫 的迴圈。

- Sonnet 依 buyer 公司背景寫個人化開發信
- Opus 扮演「收信的美國 buyer」批判這封信(太長?太罐頭?價值主張不清?)
- 不及格退回重寫,最多三輪;出迴圈後進人工覆核佇列(pending_draft)

Human-in-the-loop(架構報告設計原則 2):
發信永遠由人最終覆核 —— 本模組只產草稿,絕不直接寄送。

信件定位:**通用 B2B 開發信**。行動呼籲永遠是低摩擦的一般做生意流程
(寄樣品 / 短通話),不預設對方會參展。若 company profile 設了展會 campaign,
只在信末以一句「附帶提及」帶過(對方若也去展會可順道碰面),絕不當主 CTA
—— 多數獨立小店根本不會飛去參展,對他們喊「展會見」等於自曝罐頭信。
"""

from __future__ import annotations

import re

from .company import get_company
from .config import MODEL_MID, MODEL_TOP
from .llm import complete, complete_structured
from .models import CritiqueResult, FollowUpDrafts, Interaction, Lead

# CJK 偵測(含日文假名):開發信必須全英文,出現任何 CJK 即重寫一次
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")


def _force_english(prompt: str, first_draft: str) -> str:
    """信件含 CJK 字元時,帶著警告重生成一次(最後防線是 Opus 審稿)。"""
    if not _CJK_RE.search(first_draft):
        return first_draft
    retry = complete(
        MODEL_MID,
        prompt + "\n\nWARNING: your previous attempt contained Chinese/Japanese "
                 "characters. The ENTIRE output must be English only. Rewrite.",
        max_tokens=1024,
    )
    return retry


def _event_mention() -> str:
    """展會只當「可選的附帶提及」——有設 trade_show campaign 時,給 AI 一句軟性素材。

    回傳給 prompt 用的指示;沒設展會就回空字串(信件純一般開發流程)。
    """
    c = get_company().campaign
    if not c.is_trade_show:
        return ""
    when = f" ({c.detail})" if c.detail else ""
    return (
        f"\nOptional in-person note: we will also be at {c.name}{when}. "
        "You MAY add ONE short closing line offering to meet in person there IF "
        "they happen to attend — but this is strictly optional and must NOT be the "
        "main ask, must not assume they attend, and must not include a booth number "
        "or booking link. The primary call-to-action is always the sample/call below.\n"
    )


def draft_email(lead: Lead, hints: str | None = None) -> str:
    """Sonnet 生成個人化開發信(英文,寄給美國 buyer)。內容全部由 company profile 驅動。

    定位:通用 B2B 開發信。主 CTA 永遠是寄樣品或短通話;展會(若有設定)只當
    信末一句可選的附帶提及,不預設對方會參展。
    """
    company = get_company()
    rep_angle = lead.tier == "T0_rep"
    event_mention = _event_mention()

    prompt = (
        "Write a cold outreach email in English to open a wholesale conversation.\n\n"
        f"Sender: {company.name}, {company.description or company.industry}.\n"
        f"Value proposition: {company.value_proposition}\n"
        f"Recipient company: {lead.company}\n"
        f"Recipient: {lead.contact_name or 'the buyer'} ({lead.title or 'title unknown'})\n"
        f"What we know about them: {lead.enrichment_notes or 'nothing specific - keep it honest, do not fabricate'}\n"
        f"{event_mention}\n"
        + (
            "This recipient is an independent SALES REP GROUP, not a retailer: pitch a "
            "line-representation opportunity (commission-based, US retail relationships) "
            "instead of a wholesale purchase.\n"
            if rep_angle else
            "Pitch a wholesale/stocking conversation.\n"
        )
        + "Hard requirements:\n"
        "- The ENTIRE output must be in ENGLISH ONLY — zero Chinese or Japanese "
        "characters anywhere, including the subject line\n"
        "- Under 150 words, plain text, no bullet lists\n"
        "- One-sentence value proposition\n"
        "- A SPECIFIC reason why we reached out to THIS company (use what we know; "
        "never invent facts)\n"
        "- The main call-to-action is a normal, low-friction B2B next step: offer to "
        "send a free sample, or ask for a short call / a quick reply. Do NOT assume "
        "the recipient attends any trade show.\n"
        "- No hype words, no emoji, no generic flattery\n"
        f"- End with a simple sign-off from '{company.sender.name}'\n"
        + (f"\nReviewer feedback to address in this revision:\n{hints}\n" if hints else "")
        + "\nReturn ONLY the email body (with a subject line on the first line as "
        "'Subject: ...')."
    )
    return _force_english(prompt, complete(MODEL_MID, prompt, max_tokens=1024))


def critique_email(draft: str, lead: Lead) -> CritiqueResult:
    """Opus 扮演美國 buyer 批判信件。一封爛信毀掉一個 A 級 lead,值得用最強模型把關。"""
    result = complete_structured(
        MODEL_TOP,
        (
            f"You are a busy US retail buyer at {lead.company} "
            f"({lead.title or 'buyer'}). You get dozens of cold vendor emails a day "
            "and delete anything generic. Critique this B2B wholesale outreach email "
            "ruthlessly:\n\n"
            f"---\n{draft}\n---\n\n"
            "Fail it (verdict='revise') if ANY of these are true: contains ANY "
            "non-English text (Chinese/Japanese characters — instant fail); over "
            "150 words; reads like a template; the 'why you specifically' reason "
            "is vague or fabricated; value proposition unclear; pushy or hype-y "
            "tone; the call-to-action is high-friction or presumptuous (e.g. assumes "
            "the recipient attends a trade show, or demands a meeting). A good CTA is "
            "low-friction: offering a sample, a short call, or a quick reply. "
            "Otherwise verdict='pass'. Do NOT require any trade-show or booth "
            "reference — a normal wholesale ask with no event mention is perfectly "
            "fine. List concrete issues and give rewrite_hints if revising."
        ),
        CritiqueResult,
    )
    if result is None:
        # 解析失敗:保守處理,交人工覆核而非退回重寫
        result = CritiqueResult(verdict="pass", issues=["批判結果解析失敗,請人工加強檢查"])
    return result


def draft_follow_up(lead: Lead) -> str:
    """展中每晚批次:依當日會談紀錄生成 same-day follow-up 草稿(仍需人工掃過)。"""
    company = get_company()
    where = (f"at the {company.campaign.name} booth today"
             if company.campaign.is_trade_show else "today")
    notes = "\n".join(
        f"- {i.content}" for i in lead.interactions if i.kind == "meeting_note"
    ) or "(no meeting notes recorded)"
    prompt = (
        "Write a same-day follow-up email in ENGLISH ONLY (zero Chinese/Japanese "
        f"characters) after meeting this buyer {where}.\n\n"
        f"Company: {lead.company}\n"
        f"Contact: {lead.contact_name or 'the buyer'} ({lead.title or ''})\n"
        f"Meeting notes from today:\n{notes}\n\n"
        "Requirements: under 120 words; reference something SPECIFIC from the meeting "
        "notes (if notes are empty, keep it short and thank them for stopping by); "
        "state the concrete next step (samples / quote / call); no hype. "
        "Return only the email with 'Subject: ...' on the first line."
    )
    return _force_english(prompt, complete(MODEL_MID, prompt, max_tokens=1024))


def draft_followups(lead: Lead, seq1_draft: str) -> list[str]:
    """生成三輪序列的 seq2/3 草稿(一次呼叫,與 seq1 一起進人工覆核)。

    三輪分工(對應 exportlab 三輪跟進策略):
    - seq2(+4 工作日)= 價值信:給對方一個「值得回信」的理由
    - seq3(+6 工作日)= graceful 撤退:明說最後一封、零 CTA、開未來大門
    反幻覺鐵則:只准用已知事實,禁止編造統計數據、案例、客戶名。
    """
    company = get_company()
    prompt = (
        "You already sent the cold email below (sequence 1). The recipient has NOT "
        "replied. Write TWO follow-up emails in ENGLISH ONLY.\n\n"
        f"--- SEQUENCE 1 (already sent) ---\n{seq1_draft}\n--- END ---\n\n"
        f"Sender: {company.name} — {company.value_proposition}\n"
        f"Recipient: {lead.contact_name or 'the buyer'} at {lead.company}\n"
        f"Known facts about them: {lead.enrichment_notes or '(nothing specific)'}\n\n"
        "SEQUENCE 2 — value email (goes out 4 working days later):\n"
        "- Under 100 words. Briefly acknowledge the earlier note in ONE short clause, "
        "then add ONE new concrete angle (a specific product benefit, a relevant "
        "observation about their channel, or an offer to send something useful)\n"
        "- ANTI-FABRICATION RULE: use ONLY the facts given above. NEVER invent "
        "statistics, case studies, client names, or market data\n"
        "- Soft CTA (a one-line reply is enough); give them an easy out\n"
        "- BANNED: 'Just following up', 'Did you get a chance', 'Bumping this', "
        "'circling back', re-pitching sequence 1 verbatim\n\n"
        "SEQUENCE 3 — graceful close (goes out 6 working days after sequence 1):\n"
        "- Under 60 words. State up front this is the last email in the thread\n"
        "- ZERO call-to-action: do not ask for a call or reply\n"
        "- Leave the door open ('if this ever becomes relevant...') and wish them well\n"
        "- BANNED: guilt-tripping, self-deprecation, 'last chance' pushes, new pitches\n\n"
        "Format for BOTH: first line 'Subject: Re: <the sequence-1 subject>' to stay "
        "in the same thread, then the body, sign off as "
        f"'{company.sender.name}'. English only — zero Chinese/Japanese characters."
    )
    result = complete_structured(MODEL_MID, prompt, FollowUpDrafts)
    if result is None:
        # 結構化解析失敗:各補一次純文字呼叫(不讓 pipeline 斷掉)
        seq2 = complete(MODEL_MID, prompt + "\n\nReturn ONLY sequence 2.", max_tokens=1024)
        seq3 = complete(MODEL_MID, prompt + "\n\nReturn ONLY sequence 3.", max_tokens=1024)
    else:
        seq2, seq3 = result.seq2, result.seq3
    # CJK 防線(與 seq1 同標準;人工覆核是最後防線)
    guard = prompt + "\n\nReturn ONLY the requested single email."
    return [_force_english(guard, seq2), _force_english(guard, seq3)]


def queue_for_review(lead: Lead, draft: str, critique: CritiqueResult) -> Lead:
    """出迴圈後進人工覆核佇列:seq1 + 預生成 seq2/3 一起掛上 lead(核准一次涵蓋整串)。"""
    lead.pending_draft = draft
    try:
        lead.pending_followups = draft_followups(lead, draft)
    except Exception:  # noqa: BLE001 — 跟進信生成失敗不擋 seq1 覆核,UI 會提示補生成
        lead.pending_followups = []
    note = "通過 Opus 審稿" if not critique.issues else "審稿備註:" + ";".join(critique.issues)
    lead.interactions.append(Interaction(
        kind="email_draft",
        content=f"{draft}\n\n[{note}]",
    ))
    return lead
