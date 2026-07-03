"""CLI 入口:buyer-intel <指令>。

指令對應作戰階段:
  init        建立資料庫
  ingest      L1 擷取名單(--source apollo|places|iha|manual)
  pipeline    L2–L4 全流程(豐富 → 評分 → 信件草稿 → 覆核佇列)
  review      人工覆核信件草稿(核准後輸出到 outbox/,由人工寄送)
  followup    展中每晚批次:對當日接觸生成 same-day follow-up 草稿
  dashboard   產出 pipeline 看板 HTML
  serve       啟動展中手機 Web UI
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta

from . import db
from .adapters import ADAPTERS
from .config import OUTBOX_DIR
from .enrich import dedupe, to_lead
from .models import Interaction


def cmd_init(_args) -> None:
    db.init_db()
    print("✔ 已建立 data/leads.db")


def cmd_ingest(args) -> None:
    db.init_db()
    adapter = ADAPTERS[args.source]()
    kwargs: dict = {}
    if args.file:
        kwargs["file"] = args.file
    if args.query:
        # apollo 用 keywords、places 用 query,各自取用
        kwargs["keywords"] = args.query
        kwargs["query"] = args.query
    if args.tier:
        kwargs["tier"] = args.tier

    raw = adapter.fetch(**kwargs)
    print(f"取得 {len(raw)} 筆原始名單({args.source})")

    # 與庫內既有名單一起去重,避免重複觸達
    existing = db.all_leads()
    before = len(raw)
    merged = dedupe(raw)
    merged = [
        r for r in merged
        if not db.find_by_company_or_email(r.company, r.email)
    ]
    print(f"去重後保留 {len(merged)} 筆(移除 {before - len(merged)} 筆,庫內既有 {len(existing)} 筆)")

    for r in merged:
        db.save_lead(to_lead(r))
    print("✔ 已入庫,下一步:buyer-intel pipeline")


def cmd_pipeline(args) -> None:
    from .graph import run_pipeline  # 延遲載入:langgraph 啟動較慢

    leads = db.list_leads(stage="new")
    if args.limit:
        leads = leads[: args.limit]
    if not leads:
        print("沒有待處理的新名單(stage=new)。")
        return
    print(f"開始處理 {len(leads)} 筆名單(豐富 → 評分 → 草稿 → 覆核佇列)…")
    run_pipeline(leads)
    print("✔ 完成。下一步:buyer-intel review")


def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w\-]+", "_", name)[:60]


def cmd_review(_args) -> None:
    """人工覆核佇列:逐筆顯示草稿,核准(y)輸出 outbox/、退回(n)清除草稿。

    Human-in-the-loop:系統絕不直接寄信;核准後的檔案由人工用郵件軟體寄出,
    寄出後 lead 進入 contacted 階段並排定 5 天後追蹤。
    """
    pending = [l for l in db.all_leads() if l.pending_draft]
    if not pending:
        print("覆核佇列為空。")
        return
    OUTBOX_DIR.mkdir(exist_ok=True)
    for lead in pending:
        print("\n" + "=" * 60)
        print(f"[{lead.grade}] {lead.company} — {lead.contact_name or ''} <{lead.email or '無 email'}>")
        print(f"評分依據:{lead.score_rationale or ''}")
        print("-" * 60)
        print(lead.pending_draft)
        print("-" * 60)
        answer = input("核准並輸出到 outbox?(y=核准 / n=退回 / s=跳過)").strip().lower()
        if answer == "y":
            path = OUTBOX_DIR / f"{lead.id}_{_safe_filename(lead.company)}.txt"
            header = f"To: {lead.email or '(請補收件人)'}\n\n"
            path.write_text(header + lead.pending_draft, encoding="utf-8")
            lead.interactions.append(
                Interaction(kind="email_sent", content=lead.pending_draft)
            )
            lead.pending_draft = None
            lead.stage = "contacted"
            lead.next_action_due = date.today() + timedelta(days=5)
            db.save_lead(lead)
            print(f"✔ 已輸出 {path},請人工寄送")
        elif answer == "n":
            lead.pending_draft = None
            db.save_lead(lead)
            print("已退回(草稿清除;可重跑 pipeline 產生新稿)")
        else:
            print("跳過")


def cmd_followup(_args) -> None:
    """展中每晚批次:對當日 met_at_show 的接觸生成 follow-up 草稿進覆核佇列。"""
    from .outreach import draft_follow_up

    targets = [l for l in db.list_leads(stage="met_at_show") if not l.pending_draft]
    if not targets:
        print("沒有待跟進的展中接觸。")
        return
    for lead in targets:
        lead.pending_draft = draft_follow_up(lead)
        db.save_lead(lead)
        print(f"  ✔ {lead.company} follow-up 草稿完成")
    print(f"✔ 共 {len(targets)} 筆,執行 buyer-intel review 覆核後寄出(24 小時內!)")


def cmd_dashboard(_args) -> None:
    from .dashboard import write_dashboard

    path = write_dashboard()
    print(f"✔ 看板已輸出:{path}(瀏覽器開啟)")


def cmd_serve(args) -> None:
    import uvicorn

    db.init_db()
    print("展中模式:手機連同一 Wi-Fi,開 http://<這台電腦的IP>:%d" % args.port)
    uvicorn.run("buyer_intel.field_ops.app:app", host="0.0.0.0", port=args.port)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="buyer-intel",
        description="Ankomn Buyer Intelligence System(TIHS 2027)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="建立資料庫").set_defaults(func=cmd_init)

    p_ingest = sub.add_parser("ingest", help="L1 擷取名單")
    p_ingest.add_argument("--source", choices=list(ADAPTERS), required=True)
    p_ingest.add_argument("--query", help="搜尋條件(apollo/places)")
    p_ingest.add_argument("--file", help="CSV 路徑(iha/manual)")
    p_ingest.add_argument("--tier", choices=["T0_rep", "T1_coffee", "T2_kitchen", "T3_mass"])
    p_ingest.set_defaults(func=cmd_ingest)

    p_pipe = sub.add_parser("pipeline", help="L2–L4 全流程")
    p_pipe.add_argument("--limit", type=int, help="本次最多處理筆數(控制 API 花費)")
    p_pipe.set_defaults(func=cmd_pipeline)

    sub.add_parser("review", help="人工覆核信件草稿").set_defaults(func=cmd_review)
    sub.add_parser("followup", help="展中每晚 same-day follow-up 批次").set_defaults(func=cmd_followup)
    sub.add_parser("dashboard", help="產出 pipeline 看板").set_defaults(func=cmd_dashboard)

    p_serve = sub.add_parser("serve", help="啟動展中手機 Web UI")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"✘ {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
