"""LangGraph 編排:單筆 lead 的處理流程圖(對應架構報告第 04 節)。

  enrich → score ─(條件邊:A/B → draft;C → END)
                    draft → critique ─(pass → review;revise → draft,上限 3 輪)

- L1 ingest 在 CLI 端批次執行(run_pipeline 對每筆 lead invoke 本圖,
  等同 fan-out),與報告骨架語意一致。
- Checkpoint:SqliteSaver 持久化每個節點後的狀態;跑 500 筆斷線,
  重啟後以同一 thread_id 續跑,不重花 API 費用。
- State 一律存原生 dict(Pydantic 物件在節點內重建):checkpoint 序列化
  不含自訂型別,避免 langgraph msgpack 的相容性警告與未來版本封鎖。
- 每個節點結束時同步寫回 leads.db,人工覆核佇列隨時可查。
"""

from __future__ import annotations

import sqlite3
import threading
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from . import db
from .config import CHECKPOINT_PATH, DATA_DIR, MAX_CRITIQUE_ROUNDS
from .enrich import enrich_lead
from .models import Lead
from .outreach import critique_email, draft_email, queue_for_review
from .scoring import score_lead


class LeadState(TypedDict):
    """圖中流動的共享狀態:全部為 JSON 相容的原生型別。"""

    lead: dict            # Lead.model_dump(mode="json")
    draft: str | None
    critique: dict | None  # CritiqueResult.model_dump()
    revisions: int


def _lead(state: LeadState) -> Lead:
    return Lead.model_validate(state["lead"])


def _dump(lead: Lead) -> dict:
    return lead.model_dump(mode="json")


# ── 節點 ──

def node_enrich(state: LeadState) -> dict:
    lead = _lead(state)
    # 續跑省錢:上次跑到一半失敗的 lead,背景調查(最貴的一步)已存庫就不重查
    if lead.enrichment_notes and lead.store_count is not None:
        return {"lead": _dump(lead)}
    lead = enrich_lead(lead)
    db.save_lead(lead)
    return {"lead": _dump(lead)}


def node_score(state: LeadState) -> dict:
    lead = _lead(state)
    # 續跑省錢:已有分數與分級者不重評
    if lead.score is not None and lead.grade:
        return {"lead": _dump(lead)}
    lead = score_lead(lead)
    # 補聯絡人:僅對「過關(A/B)+ 缺 email + 有網站」者用 Hunter 反查——
    # 省額度(C 級/T3 已歸檔不碰),放評分後才知道值不值得花這次查詢
    if lead.grade in ("A", "B") and not lead.email and lead.website:
        from .enrich import backfill_contacts
        lead = backfill_contacts(lead)
    db.save_lead(lead)
    return {"lead": _dump(lead)}


def node_draft(state: LeadState) -> dict:
    hints = (state["critique"] or {}).get("rewrite_hints")
    draft = draft_email(_lead(state), hints=hints)
    return {"draft": draft, "revisions": state["revisions"] + 1}


def node_critique(state: LeadState) -> dict:
    assert state["draft"] is not None
    result = critique_email(state["draft"], _lead(state))
    return {"critique": result.model_dump()}


def node_review(state: LeadState) -> dict:
    """人工覆核佇列:草稿掛上 lead 入庫,等 CLI `review` 處理。"""
    from .models import CritiqueResult

    assert state["draft"] is not None and state["critique"] is not None
    lead = queue_for_review(
        _lead(state), state["draft"], CritiqueResult.model_validate(state["critique"])
    )
    db.save_lead(lead)
    return {"lead": _dump(lead)}


# ── 條件邊 ──

def route_by_grade(state: LeadState) -> str:
    """評分決定去向:A/B 進觸達;C(含未分級,如被歸檔的 T3)結束。"""
    return state["lead"].get("grade") or "C"


def route_by_quality(state: LeadState) -> str:
    """信不及格退回重寫;達輪數上限則強制進人工覆核(人是最後防線)。"""
    assert state["critique"] is not None
    if state["critique"]["verdict"] == "pass" or state["revisions"] >= MAX_CRITIQUE_ROUNDS:
        return "pass"
    return "revise"


# 平行 worker 各自 build_graph:建構(含 checkpoint 建表/WAL 設定)必須序列化,
# 否則多執行緒同時初始化 SQLite schema 會互撞 database is locked
_build_lock = threading.Lock()


def build_graph():
    graph = StateGraph(LeadState)

    graph.add_node("enrich", node_enrich)
    graph.add_node("score", node_score)
    graph.add_node("draft", node_draft)
    graph.add_node("critique", node_critique)
    graph.add_node("review", node_review)

    graph.set_entry_point("enrich")
    graph.add_edge("enrich", "score")
    graph.add_conditional_edges("score", route_by_grade, {
        "A": "draft", "B": "draft", "C": END,   # C 級歸檔不觸達
    })
    graph.add_edge("draft", "critique")
    graph.add_conditional_edges("critique", route_by_quality, {
        "pass": "review", "revise": "draft",
    })
    graph.add_edge("review", END)

    with _build_lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        # timeout=30:寫入撞鎖時等待而非立刻報錯
        conn = sqlite3.connect(CHECKPOINT_PATH, check_same_thread=False, timeout=30)
        checkpointer = SqliteSaver(conn)
        if hasattr(checkpointer, "setup"):
            checkpointer.setup()  # 在鎖內完成建表,worker 之間不再競速
        return graph.compile(checkpointer=checkpointer)


def prepare_batch(
    limit: int | None = None,
    state: str | None = None,
    source: str | None = None,
) -> tuple[list[Lead], list[str]]:
    """選出本批可跑的新名單:跳過待覆核者、T3 直接歸檔。回傳 (名單, 訊息)。

    - state:只跑指定州(如 IL=芝加哥所在的伊利諾州),其餘留庫不動
    - source:只跑指定來源(apollo / places / iha / manual / stockists…)
    CLI 與 Web UI 共用此入口,戰略防線只寫一份。
    """
    from .scoring import archive_t3

    messages: list[str] = []
    leads = db.list_leads(stage="new")
    # 測試 lead(🧪 開頭,寄測試信功能建立的)不進 pipeline:
    # 對不存在的公司做網路研究只會浪費額度
    test_leads = [l for l in leads if l.company.startswith("🧪")]
    if test_leads:
        messages.append(f"跳過 {len(test_leads)} 筆 🧪 測試 lead(不跑研究/寫信)")
    leads = [l for l in leads if not l.company.startswith("🧪")]
    if state:
        before = len(leads)
        leads = [l for l in leads if (l.state or "").upper() == state.upper()]
        messages.append(f"州篩選 {state.upper()}:{len(leads)} 筆(其餘 {before - len(leads)} 筆留庫)")
    if source:
        before = len(leads)
        leads = [l for l in leads if l.source == source]
        messages.append(f"來源篩選 {source}:{len(leads)} 筆(其餘 {before - len(leads)} 筆留庫)")

    pending = [l for l in leads if l.pending_draft]
    if pending:
        messages.append(f"跳過 {len(pending)} 筆待覆核名單(先去覆核佇列處理)")
    leads = [l for l in leads if not l.pending_draft]

    for lead in [l for l in leads if l.tier == "T3_mass"]:
        db.save_lead(archive_t3(lead))
        messages.append(f"⛔ {lead.company}:T3 大型量販,依戰略不主動觸達,已歸檔")
    leads = [l for l in leads if l.tier != "T3_mass"]

    if limit:
        leads = leads[:limit]
    return leads, messages


def _process_one(lead: Lead) -> Lead:
    """單筆 lead 跑完整圖。每個呼叫自建 graph(獨立 checkpoint 連線,執行緒安全)。"""
    app = build_graph()
    if lead.id is None:
        lead = db.save_lead(lead)
    state: LeadState = {
        "lead": lead.model_dump(mode="json"),
        "draft": None, "critique": None, "revisions": 0,
    }
    config = {"configurable": {"thread_id": f"lead-{lead.id}"}}
    final = app.invoke(state, config=config)
    return Lead.model_validate(final["lead"])


def run_pipeline(
    leads: list[Lead], workers: int = 3, on_message=print
) -> list[Lead]:
    """對每筆 lead 執行流程圖;thread_id 綁 lead id,斷線可續跑。

    - workers > 1 時平行處理(lead 彼此獨立),3 個 worker 約快 3 倍
    - 單筆失敗不中斷整批
    - on_message:進度訊息回呼(CLI 用 print,Web UI 導進任務日誌)
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: list[Lead] = []

    def report(done: Lead) -> None:
        results.append(done)
        on_message(f"✔ {done.company} → 分級 {done.grade or '—'}"
                    f"{',已排入覆核佇列' if done.pending_draft else ''}")

    if workers <= 1:
        for lead in leads:
            try:
                report(_process_one(lead))
            except Exception as exc:  # noqa: BLE001 — 單筆失敗不拖垮整批
                on_message(f"✘ {lead.company} 處理失敗:{exc}")
        return results

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_one, lead): lead for lead in leads}
        for future in as_completed(futures):
            lead = futures[future]
            try:
                report(future.result())
            except Exception as exc:  # noqa: BLE001
                on_message(f"✘ {lead.company} 處理失敗:{exc}")
    return results
