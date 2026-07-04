"""SQLite 存取層:單檔 leads.db,以 Pydantic 序列化存取。

設計原則(架構報告第 01 節):單人使用、單機優先,不引入 Postgres。
JSON 欄位存完整 Lead;另抽出常用查詢欄位建索引。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date

from .config import DATA_DIR, DB_PATH
from .models import Lead

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


def overdue_leads(today: date | None = None) -> list[Lead]:
    """逾期未跟進警示:next_action_due 早於今天且未歸檔。"""
    today = today or date.today()
    return [
        l for l in all_leads()
        if l.next_action_due and l.next_action_due < today and l.stage != "archived"
    ]
