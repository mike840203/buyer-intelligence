"""寄送排程器:到期的信,一次寄 1 封(L6 的心臟)。

移植 exportlab 的防風控設計,簡化為單一美國市場:
- **一次 run_once 只寄 1 封**:即使多封到期也只寄最早那封,其餘留給下一輪
  (批次爆寄是 Gmail 風控最典型的機器訊號)
- **warmup 每日上限**:前 WARMUP_WEEKS 週每天 DAILY_LIMIT_WARMUP 封,
  之後 DAILY_LIMIT_NORMAL 封;起算日 = 第一封實際寄出日(或 env 指定)
- **寄出才算數**:拿到 message_id 才標 sent;失敗標 failed 不謊報
- **bounce 防線**:同網域累積 BOUNCE_LIMIT 次失敗 → 網域加入退訂名單
- **回覆/退訂煞車**:寄送前重查 lead 狀態與退訂名單,該取消就取消

後端可插拔(config.SENDING_BACKEND):
- eml(預設):把信輸出成 outbox/*.eml(乾跑)——排程、限流、狀態機全部
  真實運作,只有「送出」這步交給人。網域暖機好之前的安全模式。
- gmail:Gmail API 自動寄出(見 gmail.py,需 OAuth 憑證)。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from .. import config, db
from ..models import Interaction, Lead, QueuedEmail
from .footer import address_ready


@dataclass
class SendResult:
    ok: bool
    message_id: str | None = None
    thread_ref: str | None = None
    error: str | None = None


# ── warmup 每日上限 ──

def _local_date(dt: datetime) -> date:
    """以 FALLBACK_TIMEZONE 為「一天」的邊界(統計每日寄出量用)。"""
    return dt.astimezone(ZoneInfo(config.FALLBACK_TIMEZONE)).date()


def warmup_start(today: date) -> date:
    """暖機起算日:env 指定優先;否則第一封實際寄出日;都沒有就是今天。

    測試信(test=True)不算——寄給自己不是 cold outreach,不啟動暖機時鐘。
    """
    if config.WARMUP_START_DATE:
        return date.fromisoformat(config.WARMUP_START_DATE)
    sent = [q.sent_at for q in db.list_queue(status="sent")
            if q.sent_at and not q.test]
    if sent:
        return min(_local_date(s) for s in sent)
    return today


def daily_limit(today: date) -> tuple[int, str]:
    """今天的寄出上限與階段標籤(warmup 第 N 週 / normal)。"""
    start = warmup_start(today)
    days = (today - start).days
    if days < config.WARMUP_WEEKS * 7:
        week = days // 7 + 1
        return config.DAILY_LIMIT_WARMUP, f"warmup 第 {week} 週"
    return config.DAILY_LIMIT_NORMAL, "normal"


def sent_today(now: datetime) -> int:
    """今日已寄出量(不含測試信——測試不佔 cold outreach 額度)。"""
    today = _local_date(now)
    return sum(
        1 for q in db.list_queue(status="sent")
        if q.sent_at and not q.test and _local_date(q.sent_at) == today
    )


# ── 寄送後端 ──

def _list_unsubscribe_header() -> str:
    """List-Unsubscribe 標頭:Gmail 會在信件頂部顯示原生「取消訂閱」按鈕。

    mailto 變體不需要 web endpoint;點按鈕 = 自動寄一封主旨 UNSUBSCRIBE 的信
    回來,由回覆偵測自動加入退訂名單。對寄達率是正面訊號。
    """
    from ..company import get_company

    sender = get_company().sender.email or ""
    return f"<mailto:{sender}?subject=UNSUBSCRIBE>"


def _send_eml(qe: QueuedEmail) -> SendResult:
    """乾跑後端:輸出標準 .eml 到 outbox/,由人用郵件軟體寄出。"""
    import re

    msg = EmailMessage()
    msg["To"] = qe.to_email
    msg["Subject"] = qe.subject
    msg["X-Unsent"] = "1"
    msg["List-Unsubscribe"] = _list_unsubscribe_header()
    msg.set_content(qe.body)
    config.OUTBOX_DIR.mkdir(exist_ok=True)
    safe = re.sub(r"[^\w\-]+", "_", qe.company)[:60]
    path = config.OUTBOX_DIR / f"{qe.lead_id}_{safe}_seq{qe.sequence_no}.eml"
    path.write_bytes(bytes(msg))
    return SendResult(ok=True, message_id=f"eml:{path.name}")


def _send_gmail(qe: QueuedEmail, thread_ref: str | None) -> SendResult:
    from . import gmail

    return gmail.send_message(
        to=qe.to_email, subject=qe.subject, body=qe.body, thread_ref=thread_ref)


def _thread_ref_of_seq1(lead_id: int) -> str | None:
    """seq2/3 要接 seq1 的 thread(同一串對話,不是每次開新信轟炸)。

    取「最新寄出」的 seq1:真實 lead 只會有一封 seq1;🧪 測試 lead 會重複
    使用、累積多封 seq1,要接最近那一串。
    """
    seq1s = [q for q in db.list_queue(status="sent", lead_id=lead_id)
             if q.sequence_no == 1 and q.thread_ref]
    if not seq1s:
        return None
    return max(seq1s, key=lambda q: q.sent_at or q.created_at).thread_ref


# ── 寄送前守門 ──

def _pre_send_guard(qe: QueuedEmail, lead: Lead | None) -> tuple[str, str] | None:
    """回傳 ("cancel"|"skip", 原因);None = 放行。狀態隨時在變,寄出前一刻重查。

    cancel = 永久取消(退訂/歸檔/對方已回應);skip = 這輪暫不寄、保留 ready
    (例:seq1 失敗待重排時,seq2/3 不能搶先出門,但也不該被殺掉)。
    """
    if lead is None:
        return ("cancel", "lead 已被刪除")
    if db.is_unsubscribed(qe.to_email):
        return ("cancel", "收件人已退訂")
    if qe.test:
        # 測試信:lead 不推進階段(stage 恆為 new),跳過後續階段檢查。
        # 回覆煞車仍有效——偵測到回信時 apply_track 會直接 cancel 剩餘 ready row。
        return None
    if lead.stage == "archived":
        return ("cancel", "lead 已歸檔")
    if qe.sequence_no > 1 and lead.stage not in ("contacted", "met_at_show"):
        if lead.stage == "new":
            # seq1 還沒成功寄出(失敗或還在排),引用「上一封」的跟進信不能先出門
            return ("skip", "seq1 尚未寄出,跟進信暫緩(seq1 寄出後恢復)")
        # 已推進到 followed_up / meeting_booked 等 = 對方有回應,跟進信不該再出門
        return ("cancel", f"lead 已推進到 {lead.stage},取消後續跟進")
    if qe.sequence_no > config.MAX_SEQUENCE:
        return ("cancel", f"超過同一收件人 {config.MAX_SEQUENCE} 封上限")
    return None


def _domain(email: str) -> str:
    return email.split("@", 1)[1].lower() if "@" in email else email


def _record_failure(qe: QueuedEmail, error: str, log: list[str]) -> None:
    qe.status = "failed"
    qe.error = error[:500]
    db.save_queued(qe)
    log.append(f"✘ {qe.company} seq{qe.sequence_no} 寄送失敗:{error[:120]}")
    # bounce 防線:同網域累積失敗達上限 → 整個網域退訂(保寄件信譽)
    domain = _domain(qe.to_email)
    failures = sum(1 for q in db.list_queue(status="failed")
                   if _domain(q.to_email) == domain)
    if failures >= config.BOUNCE_LIMIT:
        db.add_unsubscribe(domain, kind="domain", source="bounce",
                           note=f"累積 {failures} 次寄送失敗")
        log.append(f"⛔ {domain} 累積 {failures} 次失敗,已整網域加入退訂名單")


def _after_sent(qe: QueuedEmail, lead: Lead) -> None:
    """寄出成功後推進 lead 狀態(seq1 → contacted;展中 follow-up → followed_up)。"""
    lead.interactions.append(Interaction(
        kind="email_sent" if qe.sequence_no == 1 else "follow_up",
        content=f"Subject: {qe.subject}\n\n{qe.body}",
    ))
    if lead.stage == "met_at_show":
        lead.stage = "followed_up"
    elif qe.sequence_no == 1 and lead.stage == "new":
        lead.stage = "contacted"
    # 下次行動日:佇列中下一封的日期;整串寄完則 +5 天(等回覆)
    remaining = db.list_queue(status="ready", lead_id=lead.id)
    if remaining:
        lead.next_action_due = remaining[0].scheduled_at.date()
    else:
        lead.next_action_due = date.today() + timedelta(days=5)
    db.save_lead(lead)


# ── 主迴圈 ──

def run_once(now: datetime | None = None) -> list[str]:
    """一輪派送:掃到期信 → 守門 → 限流 → 只寄最早 1 封。回傳日誌。"""
    now = now or datetime.now(timezone.utc)
    log: list[str] = []

    due = db.due_ready_emails(now)

    # 守門:到期信先過濾(cancel=永久取消,不佔額度;skip=保留 ready 等下輪)
    sendable: list[QueuedEmail] = []
    for qe in due:
        lead = db.get_lead(qe.lead_id)
        verdict = _pre_send_guard(qe, lead)
        if verdict is None:
            sendable.append(qe)
        elif verdict[0] == "cancel":
            qe.status = "cancelled"
            qe.error = verdict[1]
            db.save_queued(qe)
            log.append(f"⏭ {qe.company} seq{qe.sequence_no} 取消:{verdict[1]}")
        else:  # skip:不動狀態,只記日誌
            log.append(f"⏸ {qe.company} seq{qe.sequence_no} 暫緩:{verdict[1]}")

    if not sendable:
        nxt = db.earliest_future_ready(now)
        if nxt:
            local = nxt.scheduled_at.astimezone(ZoneInfo(config.FALLBACK_TIMEZONE))
            log.append(f"⏸ 沒有到期信件。下一封:{nxt.company} seq{nxt.sequence_no} "
                       f"@ {local:%m/%d %H:%M}(buyer 當地)")
        else:
            log.append("⏸ 佇列沒有待寄信件(核准新信後會出現在這裡)")
        return log

    # 測試信 + 展中 warm follow-up 不受 cold 限流管制,優先出門
    # (測試=寄給自己;warm=對方是見過面的人——都不是 cold outreach)
    warm = [q for q in sendable
            if q.test or ((l := db.get_lead(q.lead_id)) and l.stage == "met_at_show")]
    cold = [q for q in sendable if q not in warm]

    if not warm:
        # warmup 每日限流(cold outreach 專屬)
        limit, label = daily_limit(_local_date(now))
        used = sent_today(now)
        if used >= limit:
            log.append(f"🛑 今日已達寄出上限 {used}/{limit}({label}),"
                       f"{len(cold)} 封到期信留到明天")
            return log
        # 節奏護欄:距上一封寄出未滿 INTERVAL_MIN 分鐘就等下一輪。
        # 防多個 lead 的跟進信排在同一時刻(例如都是 +4 工作日的 09:30)被連發。
        last_sent = max((q.sent_at for q in db.list_queue(status="sent")
                         if q.sent_at), default=None)
        if last_sent:
            gap = (now - last_sent).total_seconds() / 60
            if gap < config.INTERVAL_MIN_MINUTES:
                wait = int(config.INTERVAL_MIN_MINUTES - gap)
                log.append(f"⏲ 節奏護欄:距上一封僅 {gap:.0f} 分鐘"
                           f"(interval 下限 {config.INTERVAL_MIN_MINUTES}),"
                           f"約 {wait} 分鐘後寄下一封")
                return log

    # 一次只寄最早 1 封(其餘留給下一輪——這是防批次爆寄的硬規則)
    qe = warm[0] if warm else cold[0]
    lead = db.get_lead(qe.lead_id)
    assert lead is not None  # 守門已保證

    # gmail 後端 + 合規 footer 開啟時,地址沒填就不放行(CAN-SPAM 必填)
    if config.SENDING_BACKEND == "gmail" and config.ENABLE_COMPLIANCE_FOOTER \
            and not address_ready():
        log.append("🛑 company profile 的 sender.address 還沒填真實地址,"
                   "自動寄送已擋下(CAN-SPAM 必填;填好後自動恢復)")
        return log

    if config.SENDING_BACKEND == "gmail":
        thread_ref = _thread_ref_of_seq1(qe.lead_id) if qe.sequence_no > 1 else None
        try:
            result = _send_gmail(qe, thread_ref)
        except Exception as exc:  # noqa: BLE001 — 後端炸掉要進日誌不是炸掉排程器
            result = SendResult(ok=False, error=str(exc))
    else:
        result = _send_eml(qe)

    if not result.ok or not result.message_id:
        _record_failure(qe, result.error or "後端未回傳 message_id", log)
        return log

    qe.status = "sent"
    qe.message_id = result.message_id
    qe.thread_ref = result.thread_ref
    qe.sent_at = now
    db.save_queued(qe)
    if not qe.test:            # 測試信不推進 lead 狀態、不寫互動(不污染 pipeline)
        _after_sent(qe, lead)

    mode = "已寄出" if config.SENDING_BACKEND == "gmail" else "已輸出 .eml(乾跑模式,請手動寄出)"
    log.append(f"✅ {qe.company} seq{qe.sequence_no} {mode} → {result.message_id}")
    limit, label = daily_limit(_local_date(now))
    log.append(f"今日用量 {sent_today(now)}/{limit}({label});"
               f"剩餘到期 {len(sendable) - 1} 封留給下一輪")
    return log
