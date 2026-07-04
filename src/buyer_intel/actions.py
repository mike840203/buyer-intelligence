"""共用業務動作:CLI 與 Web UI 都呼叫這一層,邏輯只寫一份。

涵蓋:信件主旨拆解、.eml 輸出、核准/退回草稿、mailto 產生、階段追蹤。
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

from . import db
from .config import OUTBOX_DIR
from .models import Interaction, Lead

# 追蹤事件 → (新階段, 下次行動天數, 紀錄文字)
TRACK_EVENTS: dict[str, tuple[str, int | None, str]] = {
    "replied": ("followed_up", 2, "對方回信,對話進行中"),
    "meeting": ("meeting_booked", 7, "已敲定會議"),
    "sample": ("sample_sent", 7, "樣品已寄出,追蹤到貨與試用回饋"),
    "quote": ("quoting", 5, "報價中,追蹤決策進度"),
    "po": ("po_received", None, "🎉 收到 PO"),
    "dead": ("archived", None, "判定無望,歸檔"),
}


def safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name)[:60]


def split_subject(draft: str) -> tuple[str, str]:
    """信件草稿第一行為 'Subject: ...' 時拆出主旨與內文。"""
    lines = draft.strip().splitlines()
    if lines and lines[0].lower().startswith("subject:"):
        return lines[0][len("subject:"):].strip(), "\n".join(lines[1:]).strip()
    return "Meeting at The Inspired Home Show 2027", draft.strip()


def write_eml(lead: Lead, draft: str) -> Path:
    """核准的信輸出為 .eml(標準郵件格式,含 X-Unsent 供郵件軟體視為草稿)。"""
    subject, body = split_subject(draft)
    msg = EmailMessage()
    msg["To"] = lead.email or ""
    msg["Subject"] = subject
    msg["X-Unsent"] = "1"
    msg.set_content(body)
    OUTBOX_DIR.mkdir(exist_ok=True)
    path = OUTBOX_DIR / f"{lead.id}_{safe_filename(lead.company)}.eml"
    path.write_bytes(bytes(msg))
    return path


def approve_draft(lead: Lead, edited_draft: str | None = None) -> Path:
    """核准草稿(可帶人工修改後版本):輸出 .eml、記互動、推進階段、排跟進。"""
    draft = (edited_draft or lead.pending_draft or "").strip()
    if not draft:
        raise ValueError(f"{lead.company} 沒有待覆核的草稿")
    path = write_eml(lead, draft)
    lead.interactions.append(Interaction(kind="email_sent", content=draft))
    lead.pending_draft = None
    lead.stage = "contacted"
    lead.next_action_due = date.today() + timedelta(days=5)
    db.save_lead(lead)
    return path


def reject_draft(lead: Lead) -> None:
    """退回草稿:清空待審稿,可重跑 pipeline 產生新稿。"""
    lead.pending_draft = None
    db.save_lead(lead)


def latest_sent_email(lead: Lead) -> str | None:
    sent = [i for i in lead.interactions if i.kind == "email_sent"]
    return sent[-1].content if sent else None


def mailto_url(lead: Lead) -> str | None:
    """已核准信件 + 有收件人 → mailto 連結(瀏覽器點開即帶好草稿)。"""
    content = latest_sent_email(lead)
    if not content or not lead.email:
        return None
    subject, body = split_subject(content)
    return f"mailto:{lead.email}?subject={quote(subject)}&body={quote(body)}"


def apply_track(lead: Lead, event: str, note: str | None = None) -> Lead:
    """推進 pipeline 階段 + 自動排下次行動日 + 記錄互動。"""
    stage, due_days, label = TRACK_EVENTS[event]
    lead.stage = stage  # type: ignore[assignment]
    lead.next_action_due = (
        date.today() + timedelta(days=due_days) if due_days else None
    )
    lead.interactions.append(Interaction(
        kind="other", content=label + (f":{note}" if note else "")
    ))
    return db.save_lead(lead)
