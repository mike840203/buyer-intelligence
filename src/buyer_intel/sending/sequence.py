"""核准 → 三輪信入佇列(L6 的入口)。

人在覆核頁按一次「核准」,這裡做的事:
1. 算排程:seq1 = buyer 當地最近的寄信時段起點(同日已有排程則錯開
   INTERVAL_MIN~MAX 分鐘);seq2/3 = +4/+6 工作日的時段起點
2. 每封信附上合規 footer(或純簽名,依 config 開關)
3. 寫入 email_queue(status=ready),等 dispatcher 到期寄出
4. 清掉 lead 上的待覆核草稿

守門(寄送前最後一道靜態檢查):
- 缺 email / 已退訂 / 佇列已有同 lead 的 ready 信 → 拒絕入佇列
- 展中 follow-up(stage=met_at_show)只排一封、排「現在」,不做三輪
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from .. import config, db
from ..models import Interaction, Lead, QueuedEmail
from .footer import append_footer
from .schedule import (
    add_workdays,
    at_window_start,
    buyer_tz,
    first_send_datetime,
    parse_hhmm,
    sequence_datetimes,
)


def _split_subject(draft: str) -> tuple[str, str]:
    """首行 'Subject: ...' 拆出主旨與內文(與 actions.split_subject 同邏輯,避免循環匯入)。"""
    lines = draft.strip().splitlines()
    if lines and lines[0].lower().startswith("subject:"):
        return lines[0][len("subject:"):].strip(), "\n".join(lines[1:]).strip()
    return "(no subject)", draft.strip()


def _stagger(seq1_at: datetime, tz, window_start, window_end,
             interval_minutes: int | None) -> datetime:
    """同日已有其他排程 → 往後錯開 interval;超出寄信時段就順延到下個工作日起點。

    這是 exportlab「interval 錯開 60-90 分鐘」規則:讓寄送像真人零星寄信,
    不是同一分鐘齊發。
    """
    same_day = [
        q.scheduled_at for q in db.list_queue(status="ready")
        if q.scheduled_at.astimezone(tz).date() == seq1_at.date()
    ]
    if not same_day:
        return seq1_at
    minutes = interval_minutes if interval_minutes is not None else random.randint(
        config.INTERVAL_MIN_MINUTES, config.INTERVAL_MAX_MINUTES)
    candidate = max(max(same_day).astimezone(tz), seq1_at) + timedelta(minutes=minutes)
    if candidate.time() > window_end:
        next_day = add_workdays(candidate.date(), 1)
        candidate = at_window_start(next_day, tz, window_start)
    return candidate


def enqueue_for_lead(
    lead: Lead,
    drafts: list[str],
    now: datetime | None = None,
    interval_minutes: int | None = None,   # 測試用:固定 interval,省掉隨機
) -> list[QueuedEmail]:
    """把核准後的草稿(1~3 封)排入寄送佇列。回傳入佇列的 QueuedEmail 列表。"""
    if not lead.email:
        raise ValueError(f"{lead.company} 沒有收件人 email,不能排入寄送佇列")
    if db.is_unsubscribed(lead.email):
        raise ValueError(f"{lead.email} 在退訂名單內,依法不得再寄")
    if lead.id is None:
        lead = db.save_lead(lead)

    is_test = lead.company.startswith(TEST_COMPANY_PREFIX)
    if is_test:
        # 測試 lead 重複使用:舊的排程中測試信自動作廢(連測兩輪不堆疊)
        for stale in db.list_queue(status="ready", lead_id=lead.id):
            stale.status = "cancelled"
            stale.error = "被新一輪測試取代"
            db.save_queued(stale)
    elif db.list_queue(status="ready", lead_id=lead.id):
        raise ValueError(f"{lead.company} 已有排程中的信件(先到寄送佇列取消再重排)")

    drafts = [d for d in drafts if d and d.strip()][: config.MAX_SEQUENCE]
    if not drafts:
        raise ValueError("沒有可排程的信件內容")

    now = now or datetime.now(timezone.utc)
    tz = buyer_tz(lead.state, config.FALLBACK_TIMEZONE)
    ws, we = parse_hhmm(config.SEND_WINDOW_START), parse_hhmm(config.SEND_WINDOW_END)

    if is_test:
        # 測試序列:壓縮時程 0/+2/+4 分鐘(真實為 +4/+6 工作日),跳過時區/錯開演算
        times = [now + timedelta(minutes=m) for m in _TEST_OFFSETS_MINUTES]
    elif lead.stage == "met_at_show":
        # 展中 same-day follow-up:一封、立即(24 小時跟進是 KPI,不等隔天時段)
        times = [now]
        drafts = drafts[:1]
    else:
        seq1_at = _stagger(first_send_datetime(now, tz, ws, we), tz, ws, we,
                           interval_minutes)
        times = sequence_datetimes(seq1_at, config.FOLLOWUP_OFFSETS_WORKDAYS, tz, ws)

    queued: list[QueuedEmail] = []
    for i, draft in enumerate(drafts):
        subject, body = _split_subject(draft)
        queued.append(db.save_queued(QueuedEmail(
            lead_id=lead.id,
            company=lead.company,
            to_email=lead.email,
            subject=subject,
            body=append_footer(body),
            sequence_no=i + 1,
            scheduled_at=times[i],
            test=is_test,
        )))

    plan = "、".join(
        f"seq{q.sequence_no} {q.scheduled_at.astimezone(tz):%m/%d %H:%M}" for q in queued)
    lead.pending_draft = None
    lead.pending_followups = []
    lead.interactions.append(Interaction(
        kind="email_draft", content=f"[已核准,排入寄送佇列] {plan}(buyer 當地時間)"))
    db.save_lead(lead)
    return queued


# ── 🧪 三輪測試序列 ──

TEST_COMPANY_PREFIX = "🧪"
TEST_COMPANY = "🧪 系統測試(可刪)"

# 測試時程壓縮:真實 +4/+6 工作日 → +2/+4 分鐘(排程器 60 秒一輪,約 5 分鐘跑完整串)
_TEST_OFFSETS_MINUTES = (0, 2, 4)

_TEST_BODIES = (
    # seq1:模擬觸達信
    "Hi,\n\nThis is TEST email 1 of 3 — simulating the first-touch email a real "
    "buyer would receive.\n\nWhat to verify: it arrived, the footer below looks "
    "right, and (in Gmail) the next two emails thread under this one.\n\n"
    "You can reply to this email at any time — the remaining test emails should "
    "then be cancelled automatically (that's the reply-brake test).\n\nThe Team",
    # seq2:模擬價值跟進信(真實情境是 +4 工作日)
    "Hi,\n\nTEST email 2 of 3 — simulating the value follow-up that goes out "
    "4 working days later in production (compressed to ~2 minutes for this test).\n\n"
    "What to verify: this arrived in the SAME thread as email 1 (subject keeps "
    "'Re:'), roughly 2 minutes after it.\n\nThe Team",
    # seq3:模擬收尾信(真實情境是 +6 工作日)
    "Hi,\n\nTEST email 3 of 3 — simulating the graceful close that goes out "
    "6 working days later in production.\n\nIf all three arrived in one thread, "
    "the full sequence pipeline works end-to-end. You can now delete the test "
    "lead from the 名單 page.\n\nThe Team",
)


def queue_test_review(to_email: str):
    """建立三輪測試「草稿」進人工覆核佇列——跟真名單走同一道核准閘門。

    使用者在 /review 看到 seq1/2/3、按「核准整串」→ approve_draft →
    enqueue_for_lead 偵測到測試 lead → 壓縮時程 0/+2/+4 分鐘、test=True 入佇列。
    """
    from ..models import Lead

    to = to_email.strip().lower()
    if db.is_unsubscribed(to):
        raise ValueError(f"{to} 在退訂名單內(先到退訂名單移除,或換一個收件信箱)")

    lead = db.find_by_company_or_email(TEST_COMPANY, to)
    if lead is None:
        lead = db.save_lead(Lead(company=TEST_COMPANY, contact_name="Self Test",
                                 email=to, state="IL", source="manual",
                                 notes="測試信專用 lead,可隨時刪除"))
    else:
        lead.email = to
        lead.stage = "new"          # 前次測試可能推進過,歸零重測

    stamp = f"{datetime.now():%m/%d %H:%M}"
    base = f"TEST 3-round sequence ({stamp})"
    lead.pending_draft = f"Subject: {base}\n\n{_TEST_BODIES[0]}"
    lead.pending_followups = [
        f"Subject: Re: {base}\n\n{_TEST_BODIES[1]}",
        f"Subject: Re: {base}\n\n{_TEST_BODIES[2]}",
    ]
    return db.save_lead(lead)
