"""SQLite 存取層:單檔 leads.db,以 Pydantic 序列化存取。

設計原則(架構報告第 01 節):單人使用、單機優先,不引入 Postgres。
JSON 欄位存完整 Lead;另抽出常用查詢欄位建索引。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date

from datetime import datetime

from .config import DATA_DIR, DB_PATH
from .models import Lead, QueuedEmail

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    email TEXT,
    tier TEXT,
    region TEXT,
    grade TEXT,
    stage TEXT NOT NULL DEFAULT 'new',
    data TEXT NOT NULL              -- 完整 Lead 的 JSON
);
CREATE INDEX IF NOT EXISTS idx_leads_stage ON leads(stage);
CREATE INDEX IF NOT EXISTS idx_leads_grade ON leads(grade);
CREATE INDEX IF NOT EXISTS idx_leads_company ON leads(company);

-- L6 送後引擎:寄送佇列(一封信一列;三輪序列=同 lead_id 的 3 列)
CREATE TABLE IF NOT EXISTS email_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lead_id INTEGER NOT NULL,
    sequence_no INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    scheduled_at TEXT NOT NULL,
    to_email TEXT,
    data TEXT NOT NULL              -- 完整 QueuedEmail 的 JSON
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON email_queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_lead ON email_queue(lead_id);

-- 退訂名單:value 可為完整 email 或網域(皆小寫);寄送前必查
CREATE TABLE IF NOT EXISTS unsubscribed (
    value TEXT PRIMARY KEY,         -- email 或 domain(小寫)
    kind TEXT NOT NULL,             -- email | domain
    source TEXT,                    -- reply_keyword | bounce | manual
    note TEXT,
    created_at TEXT NOT NULL
);
"""


@contextmanager
def _conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # timeout=30:平行 pipeline 時多執行緒寫入,等待鎖而非直接報 database is locked
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript(_SCHEMA)
        # WAL 模式:允許讀寫並行,大幅降低平行 pipeline 的鎖衝突(設定會持久化)
        c.execute("PRAGMA journal_mode=WAL")


def save_lead(lead: Lead) -> Lead:
    """新增或更新一筆 lead;回傳含 id 的 Lead。"""
    with _conn() as c:
        payload = lead.model_dump_json()
        if lead.id is None:
            cur = c.execute(
                "INSERT INTO leads (company, email, tier, region, grade, stage, data) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (lead.company, lead.email, lead.tier, lead.region,
                 lead.grade, lead.stage, payload),
            )
            lead.id = cur.lastrowid
            # 回寫含 id 的 JSON,確保 data 欄位與索引欄位一致
            c.execute("UPDATE leads SET data = ? WHERE id = ?",
                      (lead.model_dump_json(), lead.id))
        else:
            c.execute(
                "UPDATE leads SET company=?, email=?, tier=?, region=?, "
                "grade=?, stage=?, data=? WHERE id=?",
                (lead.company, lead.email, lead.tier, lead.region,
                 lead.grade, lead.stage, payload, lead.id),
            )
    return lead


def get_lead(lead_id: int) -> Lead | None:
    with _conn() as c:
        row = c.execute("SELECT data FROM leads WHERE id = ?", (lead_id,)).fetchone()
    return Lead.model_validate_json(row["data"]) if row else None


def list_leads(stage: str | None = None, grade: str | None = None) -> list[Lead]:
    query = "SELECT data FROM leads WHERE 1=1"
    params: list = []
    if stage:
        query += " AND stage = ?"
        params.append(stage)
    if grade:
        query += " AND grade = ?"
        params.append(grade)
    with _conn() as c:
        rows = c.execute(query, params).fetchall()
    return [Lead.model_validate_json(r["data"]) for r in rows]


def all_leads() -> list[Lead]:
    return list_leads()


def find_by_company_or_email(company: str, email: str | None = None) -> Lead | None:
    """展中名片比對:是預約客戶還是新接觸。"""
    with _conn() as c:
        if email:
            row = c.execute(
                "SELECT data FROM leads WHERE lower(email) = lower(?)", (email,)
            ).fetchone()
            if row:
                return Lead.model_validate_json(row["data"])
        row = c.execute(
            "SELECT data FROM leads WHERE lower(company) = lower(?)", (company,)
        ).fetchone()
    return Lead.model_validate_json(row["data"]) if row else None


def delete_lead(lead_id: int) -> bool:
    """刪除單筆名單;回傳是否真的刪到東西。"""
    with _conn() as c:
        cur = c.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
        return cur.rowcount > 0


def wipe_leads() -> int:
    """刪除全部名單與寄送佇列(危險操作,僅供 UI 雙重確認流程呼叫)。回傳刪除筆數。

    刻意保留 unsubscribed 退訂名單:那是合規承諾,清掉會導致重寄給已退訂的人。
    """
    with _conn() as c:
        count = c.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        c.execute("DELETE FROM leads")
        c.execute("DELETE FROM email_queue")
    return count


def overdue_leads(today: date | None = None) -> list[Lead]:
    """逾期未跟進警示:next_action_due 早於今天且未歸檔。"""
    today = today or date.today()
    return [
        l for l in all_leads()
        if l.next_action_due and l.next_action_due < today and l.stage != "archived"
    ]


# ─────────────────────────── L6:寄送佇列 ───────────────────────────

def save_queued(qe: QueuedEmail) -> QueuedEmail:
    """新增或更新一封佇列信;回傳含 id 的 QueuedEmail。"""
    with _conn() as c:
        payload = qe.model_dump_json()
        sched = qe.scheduled_at.isoformat()
        if qe.id is None:
            cur = c.execute(
                "INSERT INTO email_queue "
                "(lead_id, sequence_no, status, scheduled_at, to_email, data) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (qe.lead_id, qe.sequence_no, qe.status, sched, qe.to_email, payload),
            )
            qe.id = cur.lastrowid
            c.execute("UPDATE email_queue SET data = ? WHERE id = ?",
                      (qe.model_dump_json(), qe.id))
        else:
            c.execute(
                "UPDATE email_queue SET lead_id=?, sequence_no=?, status=?, "
                "scheduled_at=?, to_email=?, data=? WHERE id=?",
                (qe.lead_id, qe.sequence_no, qe.status, sched, qe.to_email, payload, qe.id),
            )
    return qe


def get_queued(qid: int) -> QueuedEmail | None:
    with _conn() as c:
        row = c.execute("SELECT data FROM email_queue WHERE id = ?", (qid,)).fetchone()
    return QueuedEmail.model_validate_json(row["data"]) if row else None


def list_queue(status: str | None = None,
               lead_id: int | None = None) -> list[QueuedEmail]:
    query = "SELECT data FROM email_queue WHERE 1=1"
    params: list = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if lead_id is not None:
        query += " AND lead_id = ?"
        params.append(lead_id)
    with _conn() as c:
        rows = c.execute(query, params).fetchall()
    items = [QueuedEmail.model_validate_json(r["data"]) for r in rows]
    items.sort(key=lambda q: q.scheduled_at)
    return items


def due_ready_emails(now: datetime) -> list[QueuedEmail]:
    """status=ready 且 scheduled_at ≤ now,依到期時間升序。退訂過濾交給 dispatcher。"""
    return [q for q in list_queue(status="ready") if q.scheduled_at <= now]


def earliest_future_ready(now: datetime) -> QueuedEmail | None:
    """下一封「還沒到期」的 ready 信(UI 顯示『下次真的有信要寄』)。"""
    future = [q for q in list_queue(status="ready") if q.scheduled_at > now]
    return future[0] if future else None


def cancel_sequence(lead_id: int, from_seq: int = 1) -> int:
    """取消某 lead 佇列中 sequence_no ≥ from_seq 的 ready 信(回覆/退訂煞車)。回傳取消筆數。"""
    n = 0
    for q in list_queue(status="ready", lead_id=lead_id):
        if q.sequence_no >= from_seq:
            q.status = "cancelled"
            save_queued(q)
            n += 1
    return n


# ─────────────────────────── L6:退訂名單 ───────────────────────────

def _domain_of(email: str | None) -> str | None:
    if email and "@" in email:
        return email.split("@", 1)[1].lower()
    return None


def add_unsubscribe(value: str, kind: str = "email",
                    source: str = "manual", note: str | None = None) -> None:
    """加入退訂名單。value 為 email 或 domain(自動轉小寫);重複則覆蓋。"""
    with _conn() as c:
        c.execute(
            "INSERT INTO unsubscribed (value, kind, source, note, created_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(value) DO UPDATE SET "
            "kind=excluded.kind, source=excluded.source, note=excluded.note",
            (value.strip().lower(), kind, source, note, datetime.now().isoformat()),
        )


def is_unsubscribed(email: str | None) -> bool:
    """email 本身或其網域在退訂名單內即視為退訂。"""
    if not email:
        return False
    email = email.strip().lower()
    domain = _domain_of(email)
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM unsubscribed WHERE value = ? OR value = ? LIMIT 1",
            (email, domain or ""),
        ).fetchone()
    return row is not None


def list_unsubscribed() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT value, kind, source, note, created_at FROM unsubscribed "
            "ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]
