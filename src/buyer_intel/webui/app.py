"""Buyer Intelligence 管理介面(FastAPI,伺服器端渲染,零外部前端依賴)。

所有操作進網頁:儀表板、名單瀏覽/詳情、覆核寄信、階段追蹤、CSV 匯入、
pipeline 執行(背景任務+即時日誌)、展中名片掃描。
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from datetime import date, datetime
from html import escape as e
from pathlib import Path

from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import actions, db
from ..adapters import ManualAdapter
from ..config import ROOT
from ..enrich import dedupe, to_lead
from ..models import Interaction, Lead
from . import jobs

app = FastAPI(title="Ankomn Buyer Intelligence")

IMPORTS_DIR = ROOT / "imports"

STAGE_LABELS = {
    "new": "新名單", "contacted": "已觸達", "meeting_booked": "已約會議",
    "met_at_show": "展中接觸", "followed_up": "已跟進", "sample_sent": "樣品寄出",
    "quoting": "報價中", "po_received": "PO 到手", "archived": "已歸檔",
}
FUNNEL = [s for s in STAGE_LABELS if s != "archived"]
TRACK_LABELS = {
    "replied": "📩 對方回信", "meeting": "📅 約到會議", "sample": "📦 樣品寄出",
    "quote": "💰 報價中", "po": "🎉 收到 PO", "dead": "🗑 歸檔",
}

_CSS = """
:root{--paper:#F7F6F2;--ink:#22261F;--pine:#1F5A46;--deep:#143D30;--brass:#B98A3C;
--muted:#7C8178;--line:#DDDACF;--card:#FFF;--red:#8A3B22;}
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:'Noto Sans TC',-apple-system,sans-serif;background:var(--paper);color:var(--ink);line-height:1.7;font-size:15px;}
a{color:var(--pine);} .wrap{max-width:1100px;margin:0 auto;padding:0 20px 60px;}
header{background:var(--deep);color:#F2F0E8;}
header .wrap{display:flex;align-items:center;gap:24px;padding:0 20px;flex-wrap:wrap;}
header .logo{font-weight:700;padding:14px 0;letter-spacing:.05em;}
header nav{display:flex;gap:2px;flex-wrap:wrap;}
header nav a{color:#C9CFC5;text-decoration:none;padding:16px 12px;font-size:14px;}
header nav a.on,header nav a:hover{color:#fff;box-shadow:inset 0 -3px 0 var(--brass);}
h1{font-size:22px;margin:26px 0 14px;color:var(--deep);}
h2{font-size:17px;margin:24px 0 10px;color:var(--deep);}
.cards{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0;}
.stat{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:12px 18px;min-width:104px;text-align:center;}
.stat b{display:block;font-size:24px;color:var(--pine);} .stat span{font-size:12px;color:var(--muted);}
.card{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:18px 22px;margin:14px 0;}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);font-size:14px;margin:12px 0;}
th{background:var(--deep);color:#fff;text-align:left;padding:9px 12px;font-weight:500;font-size:13px;}
td{padding:9px 12px;border-top:1px solid var(--line);vertical-align:top;}
tr:hover td{background:#F3F1E9;}
.badge{display:inline-block;border-radius:4px;padding:1px 9px;font-size:12.5px;font-weight:700;}
.gA{background:#E2EDE4;color:#2E5C3E;} .gB{background:#F1EAD6;color:#7A5C18;}
.gC,.gN{background:#EEE;color:#777;}
.chip{display:inline-block;background:var(--paper);border:1px solid var(--line);border-radius:4px;padding:1px 8px;font-size:12px;color:var(--muted);margin-right:4px;}
.overdue{color:var(--red);font-weight:700;}
.btn{display:inline-block;background:var(--pine);color:#fff;border:0;border-radius:6px;padding:9px 18px;font-size:14px;cursor:pointer;text-decoration:none;}
.btn.sec{background:#fff;color:var(--pine);border:1px solid var(--pine);}
.btn.warn{background:var(--red);} .btn.gold{background:var(--brass);}
.btn:disabled{background:#AAA;cursor:not-allowed;}
input,select,textarea{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:#fff;}
textarea{width:100%;line-height:1.6;} form.inline{display:inline;}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:8px 0;}
pre.log{background:#12161B;color:#C9D4DE;border-radius:6px;padding:14px 16px;font-size:13px;line-height:1.65;overflow-x:auto;max-height:420px;overflow-y:auto;white-space:pre-wrap;}
.flash{background:#E2EDE4;border:1px solid #B8D4BE;border-radius:6px;padding:12px 16px;margin:14px 0;}
.flash.err{background:#F3E2DB;border-color:#DBB;}
.rationale{background:#FBFAF7;border-left:3px solid var(--brass);padding:10px 14px;font-size:13.5px;margin:8px 0;white-space:pre-wrap;}
.tl{border-left:2px solid var(--line);margin:10px 0 10px 6px;padding-left:16px;}
.tl div{margin-bottom:10px;font-size:13.5px;} .tl .when{color:var(--muted);font-size:12px;}
small.hint{color:var(--muted);}
@media(max-width:700px){td:nth-child(5),th:nth-child(5){display:none;}}
"""


def page(title: str, body: str, active: str = "", flash: str = "",
         flash_err: str = "") -> HTMLResponse:
    nav_items = [
        ("/", "儀表板"), ("/leads", "名單"), ("/review", "覆核佇列"),
        ("/import", "匯入名單"), ("/pipeline", "Pipeline"), ("/card", "名片掃描"),
    ]
    nav = "".join(
        f'<a href="{href}" class="{"on" if href == active else ""}">{label}</a>'
        for href, label in nav_items
    )
    notice = ""
    if flash:
        notice = f'<div class="flash">{flash}</div>'
    if flash_err:
        notice += f'<div class="flash err">{e(flash_err)}</div>'
    return HTMLResponse(f"""<!DOCTYPE html><html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} · Buyer Intel</title><style>{_CSS}</style></head><body>
<header><div class="wrap"><div class="logo">ANKOMN · Buyer Intel</div><nav>{nav}</nav></div></header>
<div class="wrap">{notice}{body}</div></body></html>""")


def grade_badge(lead: Lead) -> str:
    g = lead.grade or "—"
    cls = {"A": "gA", "B": "gB", "C": "gC"}.get(g, "gN")
    score = f" {lead.score:.0f}" if lead.score is not None else ""
    return f'<span class="badge {cls}">{g}{score}</span>'


def due_cell(lead: Lead) -> str:
    if not lead.next_action_due:
        return "—"
    text = lead.next_action_due.isoformat()
    if lead.next_action_due < date.today() and lead.stage != "archived":
        return f'<span class="overdue">⚠ {text}</span>'
    return text


def lead_row(lead: Lead) -> str:
    contact = e(lead.contact_name or "—")
    title = f'<br><small class="hint">{e(lead.title)}</small>' if lead.title else ""
    draft = " ✉️" if lead.pending_draft else ""
    return (f'<tr><td><a href="/leads/{lead.id}">{e(lead.company)}</a>{draft}</td>'
            f"<td>{contact}{title}</td>"
            f'<td><span class="chip">{e(lead.tier)}</span></td>'
            f'<td><span class="chip">{e(lead.region)}</span></td>'
            f"<td>{grade_badge(lead)}</td>"
            f"<td>{STAGE_LABELS.get(lead.stage, lead.stage)}</td>"
            f"<td>{due_cell(lead)}</td></tr>")


LEAD_TABLE_HEAD = ("<tr><th>公司</th><th>聯絡人</th><th>Tier</th><th>地區</th>"
                   "<th>分級</th><th>階段</th><th>下次行動</th></tr>")


def job_widget() -> str:
    snap = jobs.snapshot()
    if not snap["running"] and not snap["log"]:
        return ""
    state = "🔄 執行中" if snap["running"] else "✅ 已結束"
    tail = "<br>".join(e(line) for line in snap["log"][-3:])
    return (f'<div class="card"><b>背景任務({e(str(snap["kind"]))})— {state}</b>'
            f'<div style="font-size:13px;margin-top:6px">{tail}</div>'
            f'<a href="/pipeline">查看完整日誌 →</a></div>')


# ─────────────────────────── 儀表板 ───────────────────────────

@app.get("/", response_class=HTMLResponse)
def home():
    db.init_db()
    leads = db.all_leads()
    active = [l for l in leads if l.stage != "archived"]
    overdue = db.overdue_leads()
    pending = [l for l in leads if l.pending_draft]

    funnel = "".join(
        f'<div class="stat"><b>{sum(1 for l in active if l.stage == s)}</b>'
        f"<span>{STAGE_LABELS[s]}</span></div>"
        for s in FUNNEL
    )
    overdue_rows = "".join(lead_row(l) for l in overdue) or \
        '<tr><td colspan="7">無逾期項目 🎉</td></tr>'
    body = f"""
{job_widget()}
<h1>儀表板</h1>
<div class="cards">{funnel}</div>
<div class="row">
  <a class="btn" href="/review">✉️ 覆核佇列({len(pending)})</a>
  <a class="btn sec" href="/pipeline">▶ 執行 Pipeline</a>
  <a class="btn sec" href="/import">⬆ 匯入名單</a>
  <form class="inline" method="post" action="/followup/start">
    <button class="btn gold" type="submit">🌙 生成展中 follow-up 草稿</button>
  </form>
</div>
<h2>⚠️ 逾期未跟進({len(overdue)})</h2>
<table>{LEAD_TABLE_HEAD}{overdue_rows}</table>
<p><a href="/leads">查看全部名單({len(active)} 筆進行中)→</a></p>"""
    return page("儀表板", body, active="/")


# ─────────────────────────── 名單 ───────────────────────────

@app.get("/leads", response_class=HTMLResponse)
def leads_list(stage: str = "", grade: str = "", q: str = ""):
    leads = db.all_leads()
    if stage:
        leads = [l for l in leads if l.stage == stage]
    if grade:
        leads = [l for l in leads if (l.grade or "") == grade]
    if q:
        needle = q.lower()
        leads = [l for l in leads
                 if needle in l.company.lower()
                 or needle in (l.contact_name or "").lower()]
    leads.sort(key=lambda l: (l.grade or "Z", -(l.score or 0), l.company))

    stage_opts = '<option value="">全部階段</option>' + "".join(
        f'<option value="{s}" {"selected" if s == stage else ""}>{label}</option>'
        for s, label in STAGE_LABELS.items()
    )
    grade_opts = '<option value="">全部分級</option>' + "".join(
        f'<option value="{g}" {"selected" if g == grade else ""}>{g} 級</option>'
        for g in "ABC"
    )
    rows = "".join(lead_row(l) for l in leads) or \
        '<tr><td colspan="7">沒有符合的名單。<a href="/import">匯入名單 →</a></td></tr>'
    body = f"""
<h1>名單({len(leads)} 筆)</h1>
<form method="get" class="row">
  <select name="stage">{stage_opts}</select>
  <select name="grade">{grade_opts}</select>
  <input name="q" value="{e(q)}" placeholder="搜尋公司或聯絡人">
  <button class="btn" type="submit">篩選</button>
  <a class="btn sec" href="/leads">清除</a>
</form>
<table>{LEAD_TABLE_HEAD}{rows}</table>"""
    return page("名單", body, active="/leads")


@app.get("/leads/{lead_id}", response_class=HTMLResponse)
def lead_detail(lead_id: int, ok: str = ""):
    lead = db.get_lead(lead_id)
    if lead is None:
        return page("找不到", "<h1>找不到這筆名單</h1>", active="/leads")

    track_buttons = "".join(
        f'<form class="inline" method="post" action="/leads/{lead.id}/track">'
        f'<input type="hidden" name="event" value="{ev}">'
        f'<button class="btn sec" type="submit">{label}</button></form> '
        for ev, label in TRACK_LABELS.items()
    )
    mailto = actions.mailto_url(lead)
    send_block = ""
    if lead.pending_draft:
        send_block = f'<p>✉️ 有待覆核草稿 → <a class="btn gold" href="/review#lead-{lead.id}">前往覆核</a></p>'
    elif mailto:
        send_block = f'<p><a class="btn" href="{mailto}">📮 開啟郵件草稿寄信</a> <small class="hint">(在你的郵件軟體開啟,按下寄出即可)</small></p>'
    elif actions.latest_sent_email(lead) and not lead.email:
        send_block = '<p class="overdue">已有核准信件但缺 email——先在下方補聯絡方式</p>'

    timeline = "".join(
        f'<div><span class="when">{i.created_at:%m/%d %H:%M} · {i.kind}</span><br>'
        f"{e(i.content[:600])}</div>"
        for i in reversed(lead.interactions)
    ) or "<div>(尚無互動紀錄)</div>"

    alt_rows = "".join(
        f"<tr><td>備選 {idx + 1}</td><td>{e(alt.contact_name or '—')}</td>"
        f"<td>{e(alt.title or '—')}</td><td>{e(alt.email or '—')}</td>"
        f'<td><form class="inline" method="post" action="/leads/{lead.id}/primary/{idx}">'
        f'<button class="btn sec" type="submit">設為主收件人</button></form></td></tr>'
        for idx, alt in enumerate(lead.alt_contacts)
    )

    body = f"""
<h1>{e(lead.company)} {grade_badge(lead)}</h1>
<div class="row">
  <span class="chip">{e(lead.tier)}</span><span class="chip">{e(lead.region)}</span>
  <span class="chip">{STAGE_LABELS.get(lead.stage, lead.stage)}</span>
  <span class="chip">下次行動:{due_cell(lead)}</span>
  <span class="chip">來源:{e(lead.source)}</span>
</div>
{send_block}
<div class="card"><h2 style="margin-top:0">聯絡人(共 {1 + len(lead.alt_contacts)} 位——收件人由你決定)</h2>
<table>
<tr><th></th><th>姓名</th><th>職稱</th><th>email</th><th></th></tr>
<tr><td>⭐ 主收件人</td><td>{e(lead.contact_name or '—')}</td><td>{e(lead.title or '—')}</td>
<td>{e(lead.email or '—')} <span class="chip">{'✅ 已驗證' if lead.email_verified else '未驗證'}</span></td><td></td></tr>
{alt_rows}
</table>
<form method="post" action="/leads/{lead.id}/contact" class="row">
  <input name="contact_name" value="{e(lead.contact_name or '')}" placeholder="聯絡人">
  <input name="title" value="{e(lead.title or '')}" placeholder="職稱">
  <input name="email" value="{e(lead.email or '')}" placeholder="email" style="min-width:220px">
  <button class="btn sec" type="submit">手動更新主收件人</button>
</form></div>
<div class="card"><h2 style="margin-top:0">評分依據</h2>
<div class="rationale">{e(lead.score_rationale or '尚未評分——到 Pipeline 頁執行')}</div></div>
<div class="card"><h2 style="margin-top:0">背景情報(含備援聯絡人)</h2>
<div class="rationale">{e(lead.enrichment_notes or '尚未豐富')}</div></div>
<div class="card"><h2 style="margin-top:0">推進階段</h2>
<div class="row">{track_buttons}</div>
<form method="post" action="/leads/{lead.id}/track" class="row">
  <input type="hidden" name="event" value="replied">
  <input name="note" placeholder="補充紀錄(選填,隨任一事件送出)" style="min-width:300px">
</form></div>
<div class="card"><h2 style="margin-top:0">互動紀錄</h2><div class="tl">{timeline}</div></div>
<p><a href="/leads">← 回名單</a></p>"""
    flash = "✅ 已更新" if ok else ""
    return page(lead.company, body, active="/leads", flash=flash)


@app.post("/leads/{lead_id}/track")
def lead_track(lead_id: int, event: str = Form(...), note: str = Form("")):
    lead = db.get_lead(lead_id)
    if lead and event in actions.TRACK_EVENTS:
        actions.apply_track(lead, event, note or None)
    return RedirectResponse(f"/leads/{lead_id}?ok=1", status_code=303)


@app.post("/leads/{lead_id}/primary/{idx}")
def lead_set_primary(lead_id: int, idx: int):
    """把某位備選聯絡人升為主收件人;原主收件人退為備選,不丟任何人。"""
    from ..models import AltContact

    lead = db.get_lead(lead_id)
    if lead and 0 <= idx < len(lead.alt_contacts):
        chosen = lead.alt_contacts.pop(idx)
        if lead.contact_name or lead.email or lead.title:
            lead.alt_contacts.insert(0, AltContact(
                contact_name=lead.contact_name, title=lead.title, email=lead.email,
            ))
        lead.contact_name, lead.title = chosen.contact_name, chosen.title
        lead.email = (chosen.email or "").lower() or None
        lead.email_verified = False  # 換人要重新驗證
        db.save_lead(lead)
    return RedirectResponse(f"/leads/{lead_id}?ok=1", status_code=303)


@app.post("/leads/{lead_id}/contact")
def lead_contact(lead_id: int, contact_name: str = Form(""),
                 title: str = Form(""), email: str = Form("")):
    lead = db.get_lead(lead_id)
    if lead:
        lead.contact_name = contact_name.strip() or None
        lead.title = title.strip() or None
        new_email = email.strip().lower() or None
        if new_email != lead.email:
            lead.email = new_email
            lead.email_verified = False  # 換了信箱要重新驗證
        db.save_lead(lead)
    return RedirectResponse(f"/leads/{lead_id}?ok=1", status_code=303)


# ─────────────────────────── 覆核佇列 ───────────────────────────

@app.get("/review", response_class=HTMLResponse)
def review(approved: int = 0):
    pending = [l for l in db.all_leads() if l.pending_draft]
    flash = ""
    if approved:
        lead = db.get_lead(approved)
        if lead:
            mailto = actions.mailto_url(lead)
            link = (f' <a class="btn" href="{mailto}">📮 立即開啟郵件寄出</a>'
                    if mailto else "(此筆缺 email,補上後可寄)")
            flash = f"✅ 已核准「{e(lead.company)}」,信件已輸出 outbox/。{link}"

    cards = ""
    for lead in pending:
        cards += f"""
<div class="card" id="lead-{lead.id}">
  <h2 style="margin-top:0"><a href="/leads/{lead.id}">{e(lead.company)}</a>
    {grade_badge(lead)} <span class="chip">收件人:{e(lead.contact_name or '無聯絡人')}
    · {e(lead.email or '⚠ 缺 email')}</span>
    {f'<span class="chip">另有 {len(lead.alt_contacts)} 位備選聯絡人(詳情頁可切換)</span>' if lead.alt_contacts else ''}</h2>
  <div class="rationale">{e(lead.score_rationale or '')}</div>
  <form method="post" action="/review/{lead.id}/approve">
    <textarea name="draft" rows="12">{e(lead.pending_draft or '')}</textarea>
    <div class="row" style="margin-top:10px">
      <button class="btn" type="submit">✅ 核准(可先直接修改上方內文)</button>
    </div>
  </form>
  <form method="post" action="/review/{lead.id}/reject" class="inline">
    <button class="btn warn" type="submit">✘ 退回(重跑 pipeline 產新稿)</button>
  </form>
</div>"""
    if not cards:
        cards = '<div class="card">佇列是空的。到 <a href="/pipeline">Pipeline</a> 頁跑新名單。</div>'
    body = f"<h1>覆核佇列({len(pending)} 封)</h1>{cards}"
    return page("覆核佇列", body, active="/review", flash=flash)


@app.post("/review/{lead_id}/approve")
def review_approve(lead_id: int, draft: str = Form(...)):
    lead = db.get_lead(lead_id)
    if lead and lead.pending_draft:
        actions.approve_draft(lead, edited_draft=draft)
    return RedirectResponse(f"/review?approved={lead_id}", status_code=303)


@app.post("/review/{lead_id}/reject")
def review_reject(lead_id: int):
    lead = db.get_lead(lead_id)
    if lead:
        actions.reject_draft(lead)
    return RedirectResponse("/review", status_code=303)


# ─────────────────────────── 匯入 ───────────────────────────

@app.get("/import", response_class=HTMLResponse)
def import_form():
    body = """
<h1>匯入名單(CSV)</h1>
<div class="card">
<p>支援 <b>Apollo 網頁匯出檔</b>(欄位自動識別、姓名自動合併)與簡易格式
(company/contact_name/title/email/website/city/state/tier)。
自動去重:同公司多聯絡人依「品類買手 &gt; Owner &gt; 泛買手」保留,其餘轉備援。</p>
<form method="post" action="/import" enctype="multipart/form-data" class="row">
  <input type="file" name="file" accept=".csv" required>
  <select name="tier">
    <option value="T1_coffee">預設 Tier:T1 咖啡通路</option>
    <option value="T0_rep">T0 Rep Group</option>
    <option value="T2_kitchen">T2 廚房專賣</option>
    <option value="T3_mass">T3 大型量販(會被自動歸檔)</option>
  </select>
  <button class="btn" type="submit">匯入</button>
</form></div>"""
    return page("匯入名單", body, active="/import")


@app.post("/import", response_class=HTMLResponse)
async def import_csv(file: UploadFile, tier: str = Form("T1_coffee")):
    db.init_db()
    IMPORTS_DIR.mkdir(exist_ok=True)
    dest = IMPORTS_DIR / f"web_{datetime.now():%Y%m%d_%H%M%S}_{Path(file.filename or 'upload.csv').name}"
    dest.write_bytes(await file.read())

    raw = ManualAdapter().fetch(file=str(dest), tier=tier)
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        merged = dedupe(raw, verbose=True)
        skipped_db = []
        for r in merged:
            if db.find_by_company_or_email(r.company, r.email):
                skipped_db.append(r)
                print(f"  ✂ 略過「{r.company}」—— 資料庫已有此公司")
        merged = [r for r in merged if r not in skipped_db]
        for r in merged:
            db.save_lead(to_lead(r))
    log_text = buffer.getvalue().strip() or "(無去重紀錄)"

    body = f"""
<h1>匯入完成</h1>
<div class="cards">
  <div class="stat"><b>{len(raw)}</b><span>原始筆數</span></div>
  <div class="stat"><b>{len(merged)}</b><span>入庫筆數</span></div>
  <div class="stat"><b>{len(raw) - len(merged)}</b><span>去重/略過</span></div>
</div>
<h2>去重明細</h2><pre class="log">{e(log_text)}</pre>
<div class="row">
  <a class="btn" href="/pipeline">▶ 下一步:執行 Pipeline</a>
  <a class="btn sec" href="/leads?stage=new">查看新名單</a>
</div>"""
    return page("匯入完成", body, active="/import")


# ─────────────────────────── Pipeline ───────────────────────────

@app.get("/pipeline", response_class=HTMLResponse)
def pipeline_page():
    snap = jobs.snapshot()
    new_count = len([l for l in db.list_leads(stage="new") if not l.pending_draft])
    running = snap["running"]
    body = f"""
<h1>Pipeline(豐富 → 評分 → 寫信 → 覆核佇列)</h1>
<div class="card">
<p>待處理新名單:<b>{new_count}</b> 筆。每筆約 2–4 分鐘(會上網查該公司背景),
3 筆平行處理;啟動後可離開此頁,隨時回來看進度。</p>
<form method="post" action="/pipeline/start" class="row">
  <label>筆數上限 <input type="number" name="limit" min="1" placeholder="全部" style="width:90px"></label>
  <label>平行數 <input type="number" name="workers" value="3" min="1" max="5" style="width:70px"></label>
  <button class="btn" type="submit" {"disabled" if running else ""}>
    {"任務執行中…" if running else "▶ 開始執行"}</button>
</form></div>
<h2>任務日誌 <small class="hint">(自動更新)</small></h2>
<pre class="log" id="log">(尚無任務)</pre>
<script>
async function poll() {{
  const r = await fetch('/jobs/status'); const s = await r.json();
  const el = document.getElementById('log');
  if (s.log.length) el.textContent = s.log.join('\\n');
  if (s.running) setTimeout(poll, 2000);
  else if (s.finished_at) el.textContent += '\\n—— 結束於 ' + s.finished_at + ' ——';
}}
poll();
</script>
<div class="row"><a class="btn sec" href="/review">下一步:覆核佇列 →</a></div>"""
    return page("Pipeline", body, active="/pipeline")


@app.post("/pipeline/start")
def pipeline_start(limit: str = Form(""), workers: int = Form(3)):
    from ..graph import prepare_batch, run_pipeline

    limit_n = int(limit) if limit.strip().isdigit() else None
    workers = max(1, min(int(workers), 5))

    def job(log):
        leads, messages = prepare_batch(limit_n)
        for msg in messages:
            log(msg)
        if not leads:
            log("沒有待處理的新名單。")
            return
        log(f"開始處理 {len(leads)} 筆,{workers} 筆平行…")
        run_pipeline(leads, workers=workers, on_message=log)

    jobs.start("pipeline", job)
    return RedirectResponse("/pipeline", status_code=303)


@app.post("/followup/start")
def followup_start():
    from ..outreach import draft_follow_up

    def job(log):
        targets = [l for l in db.list_leads(stage="met_at_show") if not l.pending_draft]
        if not targets:
            log("沒有待跟進的展中接觸。")
            return
        log(f"為 {len(targets)} 筆展中接觸生成 same-day follow-up…")
        for lead in targets:
            lead.pending_draft = draft_follow_up(lead)
            db.save_lead(lead)
            log(f"✔ {lead.company} 草稿完成")
        log("全部完成——記得到覆核佇列掃過寄出(24 小時內!)")

    jobs.start("followup", job)
    return RedirectResponse("/pipeline", status_code=303)


@app.get("/jobs/status")
def jobs_status():
    return JSONResponse(jobs.snapshot())


# ─────────────────────────── 名片掃描(展中,手機) ───────────────────────────

@app.get("/card", response_class=HTMLResponse)
def card_form():
    body = """
<h1>📇 名片掃描(展中模式)</h1>
<div class="card">
<p>拍名片 → 自動建檔比對 → 產生 company brief。<b>送出後請等 1–2 分鐘</b>(AI 讀取名片並整理攻略)。</p>
<form method="post" action="/card" enctype="multipart/form-data" class="row">
  <input type="file" name="photo" accept="image/*" capture="environment" required>
  <button class="btn" type="submit">掃描並產生 brief</button>
</form>
<p><small class="hint">手機使用:連同一 Wi-Fi,開 http://&lt;電腦IP&gt;:8000/card</small></p>
</div>"""
    return page("名片掃描", body, active="/card")


@app.post("/card", response_class=HTMLResponse)
async def card_scan(photo: UploadFile):
    from ..field_ops.brief import company_brief
    from ..field_ops.ocr import extract_card

    contact = extract_card(await photo.read(), photo.content_type or "image/jpeg")
    existing = db.find_by_company_or_email(contact.company, contact.email)
    if existing:
        lead = existing
        status = "✅ 名單內既有接觸(可能是預約客戶)"
        lead.contact_name = contact.contact_name or lead.contact_name
        lead.title = contact.title or lead.title
        lead.email = contact.email or lead.email
    else:
        lead = Lead(company=contact.company, contact_name=contact.contact_name,
                    title=contact.title, email=contact.email, city=contact.city,
                    state=contact.state, source="ocr")
        status = "🆕 新接觸,已建檔"
    lead.stage = "met_at_show"
    from datetime import timedelta
    lead.next_action_due = date.today() + timedelta(days=1)
    lead = db.save_lead(lead)
    brief = company_brief(lead)

    body = f"""
<h1>{e(lead.company)}</h1>
<div class="row"><span class="chip">{status}</span>
<span class="chip">{e(lead.tier)}</span>{grade_badge(lead)}</div>
<div class="card"><div class="rationale">{e(brief)}</div></div>
<div class="card"><h2 style="margin-top:0">會談重點</h2>
<form method="post" action="/card/note/{lead.id}">
  <textarea name="note" rows="4" placeholder="談了什麼、對方要什麼、下一步"></textarea>
  <div class="row" style="margin-top:8px"><button class="btn" type="submit">存檔</button></div>
</form></div>
<p><a class="btn sec" href="/card">← 掃下一張</a></p>"""
    return page(lead.company, body, active="/card")


@app.post("/card/note/{lead_id}", response_class=HTMLResponse)
def card_note(lead_id: int, note: str = Form(...)):
    lead = db.get_lead(lead_id)
    if lead:
        lead.interactions.append(Interaction(kind="meeting_note", content=note))
        db.save_lead(lead)
    return RedirectResponse("/card", status_code=303)
