"""LangGraph 編排:單筆 lead 的處理流程圖(對應架構報告第 04 節)。

  enrich → score ─(條件邊:A/B → draft;C → END)
                    draft → critique ─(pass → review;revise → draft,上限 3 輪)

- L1 ingest 在 CLI 端批次執行(run_pipeline 對每筆 lead invoke 本圖,
  等同 fan-out),與報告骨架語意一致。
- Checkpoint:SqliteSaver 持久化每個節點後的狀態;跑 500 筆斷線,
  重啟後以同一 thread_id 續跑,不重花 API 費用。
- 每個節點結束時同步寫回 leads.db,人工覆核佇列隨時可查。
"""

from __future__ import annotations

import sqlite3
from typing import TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from . import db
from .config import CHECKPOINT_PATH, DATA_DIR, MAX_CRITIQUE_ROUNDS
from .enrich import enrich_lead
from .models import CritiqueResult, Lead
from .outreach import critique_email, draft_email, queue_for_review
from .scoring import score_lead


class LeadState(TypedDict):
    """圖中流動的共享狀態:一筆 lead 及其信件草稿與批判結果。"""

    lead: Lead
    draft: str | None
    critique: CritiqueResult | None
    revisions: int


# ── 節點 ──

def node_enrich(state: LeadState) -> dict:
    lead = enrich_lead(state["lead"])
    db.save_lead(lead)
    return {"lead": lead}


def node_score(state: LeadState) -> dict:
    lead = score_lead(state["lead"])
    db.save_lead(lead)
    return {"lead": lead}


def node_draft(state: LeadState) -> dict:
    hints = state["critique"].rewrite_hints if state["critique"] else None
    draft = draft_email(state["lead"], hints=hints)
    return {"draft": draft, "revisions": state["revisions"] + 1}


def node_critique(state: LeadState) -> dict:
    assert state["draft"] is not None
    return {"critique": critique_email(state["draft"], state["lead"])}


def node_review(state: LeadState) -> dict:
    """人工覆核佇列:草稿掛上 lead 入庫,等 CLI `review` 處理。"""
    assert state["draft"] is not None and state["critique"] is not None
    lead = queue_for_review(state["lead"], state["draft"], state["critique"])
    db.save_lead(lead)
    return {"lead": lead}


# ── 條件邊 ──

def route_by_grade(state: LeadState) -> str:
    """評分決定去向:A/B 進觸達;C 歸檔。"""
    return state["lead"].grade or "C"


def route_by_quality(state: LeadState) -> str:
    """信不及格退回重寫;達輪數上限則強制進人工覆核(人是最後防線)。"""
    assert state["critique"] is not None
    if state["critique"].verdict == "pass" or state["revisions"] >= MAX_CRITIQUE_ROUNDS:
        return "pass"
    return "revise"


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

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    checkpointer = SqliteSaver(
        sqlite3.connect(CHECKPOINT_PATH, check_same_thread=False)
    )
    return graph.compile(checkpointer=checkpointer)


def run_pipeline(leads: list[Lead]) -> list[Lead]:
    """對每筆 lead 執行流程圖;thread_id 綁 lead id,斷線可續跑。"""
    app = build_graph()
    results: list[Lead] = []
    for lead in leads:
        if lead.id is None:
            lead = db.save_lead(lead)
        state: LeadState = {
            "lead": lead, "draft": None, "critique": None, "revisions": 0,
        }
        config = {"configurable": {"thread_id": f"lead-{lead.id}"}}
        final = app.invoke(state, config=config)
        results.append(final["lead"])
        print(f"  ✔ {lead.company} → 分級 {final['lead'].grade or '—'}"
              f"{',已排入覆核佇列' if final['lead'].pending_draft else ''}")
    return results
