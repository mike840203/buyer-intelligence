"""CLI 入口:buyer-intel <指令>。

日常操作建議改用 Web UI:`buyer-intel serve` 後開 http://localhost:8000,
名單、覆核、寄信、追蹤、匯入、pipeline 全部在網頁完成。
CLI 保留給腳本化與離線操作;業務邏輯統一在 actions.py / graph.py,兩邊共用。
"""

from __future__ import annotations

import argparse
import sys

from . import actions, db
from .adapters import ADAPTERS
from .enrich import dedupe, to_lead


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
        kwargs["keywords"] = args.query   # apollo 用
        kwargs["query"] = args.query      # places 用
    if args.tier:
        kwargs["tier"] = args.tier

    raw = adapter.fetch(**kwargs)
    print(f"取得 {len(raw)} 筆原始名單({args.source})")

    existing = db.all_leads()
    before = len(raw)
    merged = dedupe(raw, verbose=True)
    in_db = [r for r in merged if db.find_by_company_or_email(r.company, r.email)]
    for r in in_db:
        print(f"  ✂ 略過「{r.company}」—— 資料庫已有此公司")
    merged = [r for r in merged if r not in in_db]
    print(f"去重後保留 {len(merged)} 筆(移除 {before - len(merged)} 筆,"
          f"庫內既有 {len(existing)} 筆)")

    for r in merged:
        db.save_lead(to_lead(r))
    print("✔ 已入庫,下一步:buyer-intel pipeline(或到 Web UI 操作)")


def cmd_pipeline(args) -> None:
    from .graph import prepare_batch, run_pipeline  # 延遲載入:langgraph 啟動較慢

    leads, messages = prepare_batch(args.limit, state=args.state, source=args.source)
    for msg in messages:
        print(f"  {msg}")
    if not leads:
        print("沒有待處理的新名單(stage=new)。")
        return
    print(f"開始處理 {len(leads)} 筆名單,{args.workers} 筆平行"
          f"(豐富 → 評分 → 草稿 → 覆核佇列)…")
    run_pipeline(leads, workers=args.workers,
                 on_message=lambda m: print(f"  {m}"))
    print("✔ 完成。下一步:buyer-intel review")


def cmd_review(_args) -> None:
    """人工覆核佇列(終端機版;Web UI 的 /review 功能相同且可直接改稿)。"""
    pending = [l for l in db.all_leads() if l.pending_draft]
    if not pending:
        print("覆核佇列為空。")
        return
    for lead in pending:
        print("\n" + "=" * 60)
        print(f"[{lead.grade}] {lead.company} — {lead.contact_name or ''} "
              f"<{lead.email or '無 email'}>")
        print(f"評分依據:{lead.score_rationale or ''}")
        print("-" * 60)
        print(lead.pending_draft)
        for i, fu in enumerate(lead.pending_followups, start=2):
            print(f"\n--- 跟進信 seq{i}(自動排 +{'4' if i == 2 else '6'} 工作日)---")
            print(fu)
        print("-" * 60)
        answer = input("核准整串並排入寄送佇列?(y=核准 / n=退回 / s=跳過)").strip().lower()
        if answer == "y":
            try:
                queued = actions.approve_draft(lead)
            except ValueError as exc:
                print(f"✘ {exc}")
                continue
            print(f"✔ 已排入寄送佇列 {len(queued)} 封:")
            for q in queued:
                print(f"  seq{q.sequence_no} → {q.scheduled_at:%m/%d %H:%M %Z}")
            print("  到期後由排程器自動處理(buyer-intel dispatch 可手動跑一輪)")
        elif answer == "n":
            actions.reject_draft(lead)
            print("已退回(草稿清除;可重跑 pipeline 產生新稿)")
        else:
            print("跳過")


def cmd_send(args) -> None:
    """一鍵寄信:mailto 開啟預設郵件軟體(寄送鍵永遠由人按)。"""
    import webbrowser

    lead = db.get_lead(args.id)
    if lead is None:
        raise ValueError(f"找不到 lead #{args.id}")
    url = actions.mailto_url(lead)
    if url is None:
        raise ValueError(
            f"{lead.company} 缺 email 或尚無核准信件(先 review,再補聯絡人)")
    webbrowser.open(url)
    print(f"✔ 已開啟郵件草稿:{lead.company} <{lead.email}>——確認後按下寄出")


def cmd_track(args) -> None:
    lead = db.get_lead(args.id)
    if lead is None:
        raise ValueError(f"找不到 lead #{args.id}")
    lead = actions.apply_track(lead, args.event, args.note)
    due = f",下次行動 {lead.next_action_due}" if lead.next_action_due else ""
    print(f"✔ {lead.company} → {lead.stage}{due}")


def cmd_dispatch(_args) -> None:
    """手動跑一輪寄送排程器(Web UI 的背景排程器做同一件事)。"""
    from .sending.dispatcher import run_once

    for line in run_once():
        print(f"  {line}")


def cmd_followup(_args) -> None:
    from .outreach import draft_follow_up

    targets = [l for l in db.list_leads(stage="met_at_show") if not l.pending_draft]
    if not targets:
        print("沒有待跟進的展中接觸。")
        return
    for lead in targets:
        lead.pending_draft = draft_follow_up(lead)
        db.save_lead(lead)
        print(f"  ✔ {lead.company} follow-up 草稿完成")
    print(f"✔ 共 {len(targets)} 筆,覆核後寄出(24 小時內!)")


def cmd_dashboard(_args) -> None:
    from .dashboard import write_dashboard

    path = write_dashboard()
    print(f"✔ 看板已輸出:{path}(Web UI 首頁有即時版)")


def cmd_serve(args) -> None:
    import uvicorn

    db.init_db()
    print("═" * 56)
    print("  Buyer Intelligence Web UI")
    print(f"  本機:http://localhost:{args.port}")
    print(f"  手機(展中名片掃描):http://<這台電腦的IP>:{args.port}/card")
    print("═" * 56)
    uvicorn.run("buyer_intel.webui.app:app", host="0.0.0.0", port=args.port)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="buyer-intel",
        description="Buyer Intelligence System(公司身分見 company/*.toml)。"
                    "日常操作建議:buyer-intel serve → 瀏覽器 http://localhost:8000",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="啟動 Web UI(建議的日常入口)")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.set_defaults(func=cmd_serve)

    sub.add_parser("init", help="建立資料庫").set_defaults(func=cmd_init)

    p_ingest = sub.add_parser("ingest", help="L1 擷取名單")
    p_ingest.add_argument("--source", choices=list(ADAPTERS), required=True)
    p_ingest.add_argument("--query", help="搜尋條件(apollo/places)")
    p_ingest.add_argument("--file", help="CSV 路徑(iha/manual)")
    p_ingest.add_argument("--tier", choices=["T0_rep", "T1_coffee", "T2_kitchen", "T3_mass"])
    p_ingest.set_defaults(func=cmd_ingest)

    p_pipe = sub.add_parser("pipeline", help="L2–L4 全流程")
    p_pipe.add_argument("--limit", type=int, help="本次最多處理筆數(控制額度)")
    p_pipe.add_argument("--workers", type=int, default=3,
                        help="平行處理筆數(預設 3;撞訂閱限流就降 1)")
    p_pipe.add_argument("--state", help="只跑指定州(縮寫,如 IL、WA、TX)")
    p_pipe.add_argument("--source", help="只跑指定來源(apollo / places / …)")
    p_pipe.set_defaults(func=cmd_pipeline)

    sub.add_parser("review", help="人工覆核信件草稿").set_defaults(func=cmd_review)

    p_send = sub.add_parser("send", help="一鍵開啟已核准信件的郵件草稿")
    p_send.add_argument("id", type=int, help="lead 編號")
    p_send.set_defaults(func=cmd_send)

    p_track = sub.add_parser("track", help="推進 pipeline 階段")
    p_track.add_argument("id", type=int, help="lead 編號")
    p_track.add_argument("event", choices=list(actions.TRACK_EVENTS), help="發生了什麼")
    p_track.add_argument("--note", help="補充紀錄(選填)")
    p_track.set_defaults(func=cmd_track)

    sub.add_parser("dispatch", help="手動跑一輪寄送排程器(到期信一次寄 1 封)").set_defaults(func=cmd_dispatch)
    sub.add_parser("followup", help="展中每晚 same-day follow-up 批次").set_defaults(func=cmd_followup)
    sub.add_parser("dashboard", help="產出靜態看板 HTML").set_defaults(func=cmd_dashboard)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"✘ {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
