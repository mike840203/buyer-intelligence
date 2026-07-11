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
from . import jobs, scheduler

app = FastAPI(title="Buyer Intelligence")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    scheduler.start_background()   # 背景寄送排程器:UI 開著就會自動派送到期信

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
# 資料來源顯示名稱(匯入時可自選標籤,pipeline 可依來源篩選)
SOURCE_LABELS = {
    "apollo": "Apollo", "places": "Google 地圖", "iha": "IHA 展場",
    "linkedin": "LinkedIn", "stockists": "競品 Stockists",
    "importyeti": "ImportYeti 海關", "manual": "手動/CSV", "ocr": "展中名片",
}


def source_chip(source: str) -> str:
    return f'<span class="chip">{e(SOURCE_LABELS.get(source, source))}</span>'


def linkedin_check_link(name: str | None, company: str) -> str:
    """LinkedIn 在職查核連結:寄信前 30 秒人工確認本人還在職、職稱沒變。

    用 Google 搜尋(合規)而非爬蟲——自動化抓 LinkedIn 違反其條款且有封號風險。
    """
    from urllib.parse import quote_plus

    if not name:
        return ""
    query = quote_plus(f'"{name}" "{company}" LinkedIn')
    return (f'<a href="https://www.google.com/search?q={query}" target="_blank" '
            f'rel="noopener">🔍 LinkedIn 在職查核</a>')

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


def _brand() -> str:
    from ..company import get_company
    return get_company().name.upper()


def page(title: str, body: str, active: str = "", flash: str = "",
         flash_err: str = "") -> HTMLResponse:
    nav_items = [
        ("/", "儀表板"), ("/leads", "名單"), ("/review", "覆核佇列"),
        ("/outbox", "寄送佇列"), ("/import", "匯入名單"), ("/pipeline", "Pipeline"),
        ("/card", "名片掃描"),
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
<header><div class="wrap"><div class="logo">{e(_brand())} · Buyer Intel</div><nav>{nav}</nav></div></header>
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
            f'<td><span class="chip">{e(lead.state or "?")}</span></td>'
            f"<td>{source_chip(lead.source)}</td>"
            f'<td><span class="chip">{e(lead.tier)}</span></td>'
            f"<td>{grade_badge(lead)}</td>"
            f"<td>{STAGE_LABELS.get(lead.stage, lead.stage)}</td>"
            f"<td>{due_cell(lead)}</td></tr>")


LEAD_TABLE_HEAD = ("<tr><th>公司</th><th>聯絡人</th><th>州</th><th>來源</th>"
                   "<th>Tier</th><th>分級</th><th>階段</th><th>下次行動</th></tr>")


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
def home(wiped: int = 0):
    db.init_db()
    leads = db.all_leads()
    active = [l for l in leads if l.stage != "archived"]
    overdue = db.overdue_leads()
    pending = [l for l in leads if l.pending_draft]
    ready_count = len(db.list_queue(status="ready"))

    funnel = "".join(
        f'<div class="stat"><b>{sum(1 for l in active if l.stage == s)}</b>'
        f"<span>{STAGE_LABELS[s]}</span></div>"
        for s in FUNNEL
    )
    overdue_rows = "".join(lead_row(l) for l in overdue) or \
        '<tr><td colspan="8">無逾期項目 🎉</td></tr>'
    body = f"""
{job_widget()}
<h1>儀表板</h1>
<div class="cards">{funnel}</div>
<div class="row">
  <a class="btn" href="/review">✉️ 覆核佇列({len(pending)})</a>
  <a class="btn gold" href="/outbox">📤 寄送佇列({ready_count} 排程中)</a>
  <a class="btn sec" href="/pipeline">▶ 執行 Pipeline</a>
  <a class="btn sec" href="/import">⬆ 匯入名單</a>
  <form class="inline" method="post" action="/followup/start">
    <button class="btn sec" type="submit">🌙 生成展中 follow-up 草稿</button>
  </form>
</div>
<h2>⚠️ 逾期未跟進({len(overdue)})</h2>
<table>{LEAD_TABLE_HEAD}{overdue_rows}</table>
<p><a href="/leads">查看全部名單({len(active)} 筆進行中)→</a></p>
<div class="card" style="border-color:#DBB;margin-top:36px">
  <b style="color:var(--red)">危險區</b>
  <span class="chip">目前 {len(leads)} 筆名單</span>
  <a class="btn warn" href="/wipe" style="margin-left:10px">🗑 清空全部資料…</a>
  <small class="hint">(需輸入確認字才會執行)</small>
</div>"""
    flash = f"✅ 已清空 {wiped} 筆名單(imports/ 的 CSV 原檔保留)" if wiped else ""
    return page("儀表板", body, active="/", flash=flash)


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
        '<tr><td colspan="8">沒有符合的名單。<a href="/import">匯入名單 →</a></td></tr>'
    body = f"""
<h1>名單({len(leads)} 筆)</h1>
<form method="get" class="row">
  <select name="stage" onchange="this.form.submit()">{stage_opts}</select>
  <select name="grade" onchange="this.form.submit()">{grade_opts}</select>
  <input name="q" value="{e(q)}" placeholder="搜尋公司或聯絡人(按 Enter)">
  <button class="btn" type="submit">篩選</button>
  <a class="btn sec" href="/leads">清除</a>
</form>
{f'<p><small class="hint">目前篩選:{STAGE_LABELS.get(stage, "")} {f"{grade} 級" if grade else ""} {f"關鍵字「{e(q)}」" if q else ""} — 共 {len(leads)} 筆符合</small></p>' if (stage or grade or q) else ''}
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
    queued = db.list_queue(lead_id=lead_id) if lead.id else []
    ready_q = [q for q in queued if q.status == "ready"]
    send_block = ""
    if lead.pending_draft:
        send_block = f'<p>✉️ 有待覆核草稿 → <a class="btn gold" href="/review#lead-{lead.id}">前往覆核</a></p>'
    elif ready_q:
        plan = "、".join(f"seq{q.sequence_no} {q.scheduled_at:%m/%d %H:%M}" for q in ready_q)
        send_block = (f'<p>⏳ 已排程 {len(ready_q)} 封:{plan} '
                      f'<a class="btn sec" href="/outbox">寄送佇列 →</a></p>')
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
  <span class="chip">{e(lead.tier)}</span>
  <span class="chip">州:{e(lead.state or '?')}{f' · {e(lead.city)}' if lead.city else ''}</span>
  <span class="chip">{STAGE_LABELS.get(lead.stage, lead.stage)}</span>
  <span class="chip">下次行動:{due_cell(lead)}</span>
  {source_chip(lead.source)}
</div>
{send_block}
<div class="card"><h2 style="margin-top:0">聯絡人(共 {1 + len(lead.alt_contacts)} 位——收件人由你決定)</h2>
<table>
<tr><th></th><th>姓名</th><th>職稱</th><th>email</th><th></th></tr>
<tr><td>⭐ 主收件人</td><td>{e(lead.contact_name or '—')}</td><td>{e(lead.title or '—')}</td>
<td>{e(lead.email or '—')} <span class="chip">{'✅ 已驗證' if lead.email_verified else '未驗證'}</span></td>
<td>{linkedin_check_link(lead.contact_name, lead.company)}</td></tr>
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
<div class="row">
  <a href="/leads">← 回名單</a>
  <form class="inline" method="post" action="/leads/{lead.id}/delete"
        onsubmit="return confirm('確定永久刪除「{e(lead.company)}」?此操作無法復原。')">
    <button class="btn warn" type="submit" style="margin-left:24px">🗑 刪除此筆名單</button>
  </form>
</div>"""
    flash = "✅ 已更新" if ok else ""
    return page(lead.company, body, active="/leads", flash=flash)


@app.post("/leads/{lead_id}/track")
def lead_track(lead_id: int, event: str = Form(...), note: str = Form("")):
    lead = db.get_lead(lead_id)
    if lead and event in actions.TRACK_EVENTS:
        actions.apply_track(lead, event, note or None)
    return RedirectResponse(f"/leads/{lead_id}?ok=1", status_code=303)


def _try_verify(email: str | None) -> bool:
    """單筆即時驗證(手動改信箱/換人時用);失敗不阻擋,標未驗證即可。"""
    from ..enrich import verify_email

    if not email:
        return False
    try:
        return verify_email(email)
    except Exception:  # noqa: BLE001
        return False


@app.post("/leads/{lead_id}/delete")
def lead_delete(lead_id: int):
    """單筆刪除(前端已跳確認對話框)。"""
    lead = db.get_lead(lead_id)
    if lead:
        db.delete_lead(lead_id)
    return RedirectResponse("/leads", status_code=303)


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
        lead.email_verified = _try_verify(lead.email)  # 換人即時重驗(單筆約 1 秒)
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
            lead.email_verified = _try_verify(new_email)  # 換信箱即時重驗
        db.save_lead(lead)
    return RedirectResponse(f"/leads/{lead_id}?ok=1", status_code=303)


# ─────────────────────────── 覆核佇列 ───────────────────────────

def _review_card(lead: Lead) -> str:
    """單封覆核卡片(seq1 + 預生成 seq2/3)。"""
    is_test = lead.company.startswith("🧪")
    followup_boxes = ""
    if lead.pending_followups:
        for i, fu in enumerate(lead.pending_followups):
            seq = i + 2
            if is_test:
                off = f"+{2 if seq == 2 else 4} 分鐘(測試壓縮;真實為 +{4 if seq == 2 else 6} 工作日)"
            else:
                off = "+4 工作日" if seq == 2 else "+6 工作日"
            followup_boxes += (
                f'<p style="margin:10px 0 4px"><b>跟進信 seq{seq}</b> '
                f'<span class="chip">{off},對方回信會自動取消</span></p>'
                f'<textarea name="followup{seq}" rows="8">{e(fu)}</textarea>')
    else:
        followup_boxes = ('<p><small class="hint">⚠ 此筆沒有預生成跟進信'
                          '(舊資料),核准只會排 seq1;重跑 pipeline 可得三輪。'
                          '</small></p>')
    return f"""
<div class="card" id="lead-{lead.id}">
  <h2 style="margin-top:0"><a href="/leads/{lead.id}">{e(lead.company)}</a>
    {grade_badge(lead)} <span class="chip">收件人:{e(lead.contact_name or '無聯絡人')}
    · {e(lead.email or '⚠ 缺 email')}</span>
    {f'<span class="chip">另有 {len(lead.alt_contacts)} 位備選聯絡人(詳情頁可切換)</span>' if lead.alt_contacts else ''}
    {linkedin_check_link(lead.contact_name, lead.company)}</h2>
  <div class="rationale">{e(lead.score_rationale or '')}</div>
  <form method="post" action="/review/{lead.id}/approve">
    <p style="margin:4px 0"><b>第一封 seq1</b> <span class="chip">核准後排最近的寄信時段</span></p>
    <textarea name="draft" rows="12">{e(lead.pending_draft or '')}</textarea>
    {followup_boxes}
    <div class="row" style="margin-top:10px">
      <button class="btn" type="submit">✅ 核准整串(三封都可先改;按一次管到底)</button>
    </div>
  </form>
  <form method="post" action="/review/{lead.id}/reject" class="inline">
    <button class="btn warn" type="submit">✘ 退回(重跑 pipeline 產新稿)</button>
  </form>
</div>"""


@app.get("/review", response_class=HTMLResponse)
def review(approved: int = 0, err: str = "", msg: str = "", pick: int = 0):
    pending = [l for l in db.all_leads() if l.pending_draft]
    flash = msg or ""
    if approved:
        lead = db.get_lead(approved)
        if lead:
            queued = db.list_queue(lead_id=approved)
            plan = "、".join(f"seq{q.sequence_no} {q.scheduled_at:%m/%d %H:%M}"
                             for q in queued if q.status == "ready")
            flash = (f"✅ 已核准「{e(lead.company)}」整串,排入寄送佇列:{plan} "
                     f'<a class="btn sec" href="/outbox">查看寄送佇列 →</a>')

    if not pending:
        body = ("<h1>覆核佇列(0 封)</h1><div class=\"card\">佇列是空的。"
                "到 <a href=\"/pipeline\">Pipeline</a> 頁跑新名單。</div>")
        return page("覆核佇列", body, active="/review", flash=flash, flash_err=err)

    # 下拉選單挑一封:預設第一封;核准後自動跳下一封;pick 指定則看那封
    pending_ids = [l.id for l in pending]
    current_id = pick if pick in pending_ids else pending_ids[0]
    current = next(l for l in pending if l.id == current_id)

    options = "".join(
        f'<option value="{l.id}" {"selected" if l.id == current_id else ""}>'
        f'{i + 1}/{len(pending)} · [{l.grade or "—"}] {e(l.company)}'
        f'{" ⚠缺email" if not l.email else ""}</option>'
        for i, l in enumerate(pending)
    )
    idx = pending_ids.index(current_id)
    prev_link = (f'<a class="btn sec" href="/review?pick={pending_ids[idx - 1]}">← 上一封</a>'
                 if idx > 0 else '<span class="btn sec" style="opacity:.4">← 上一封</span>')
    next_link = (f'<a class="btn sec" href="/review?pick={pending_ids[idx + 1]}">下一封 →</a>'
                 if idx < len(pending) - 1 else '<span class="btn sec" style="opacity:.4">下一封 →</span>')

    body = f"""
<h1>覆核佇列({len(pending)} 封待審)</h1>
<div class="card">
  <form method="get" action="/review" class="row" style="align-items:center">
    <label><b>挑一封覆核:</b>
      <select name="pick" onchange="this.form.submit()" style="min-width:320px">
        {options}
      </select></label>
    {prev_link}{next_link}
  </form>
</div>
{_review_card(current)}"""
    return page("覆核佇列", body, active="/review", flash=flash, flash_err=err)


def _next_pending_id(after_id: int) -> int:
    """核准/退回一封後,自動指向下一封待覆核(讓使用者一封接一封看)。"""
    remaining = [l.id for l in db.all_leads() if l.pending_draft and l.id != after_id]
    return remaining[0] if remaining else 0


@app.post("/review/{lead_id}/approve")
def review_approve(lead_id: int, draft: str = Form(...),
                   followup2: str = Form(""), followup3: str = Form("")):
    lead = db.get_lead(lead_id)
    if lead and lead.pending_draft:
        followups = [f for f in (followup2, followup3) if f.strip()]
        try:
            actions.approve_draft(lead, edited_draft=draft,
                                  edited_followups=followups)
        except ValueError as exc:
            from urllib.parse import quote
            return RedirectResponse(f"/review?err={quote(str(exc))}", status_code=303)
    nxt = _next_pending_id(lead_id)
    return RedirectResponse(f"/review?approved={lead_id}&pick={nxt}", status_code=303)


@app.post("/review/{lead_id}/reject")
def review_reject(lead_id: int):
    lead = db.get_lead(lead_id)
    if lead:
        actions.reject_draft(lead)
    return RedirectResponse(f"/review?pick={_next_pending_id(lead_id)}", status_code=303)


# ─────────────────────────── 寄送佇列(L6) ───────────────────────────

QUEUE_STATUS_LABELS = {"ready": "⏳ 排程中", "sent": "✅ 已寄出",
                       "cancelled": "⏭ 已取消", "failed": "✘ 失敗"}


@app.get("/outbox", response_class=HTMLResponse)
def outbox(msg: str = "", err: str = ""):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from ..config import (DAILY_LIMIT_NORMAL, ENABLE_COMPLIANCE_FOOTER,
                          FALLBACK_TIMEZONE, SENDING_BACKEND, WARMUP_WEEKS)
    from ..sending import dispatcher
    from ..sending.footer import address_ready

    now = datetime.now(timezone.utc)
    tz = ZoneInfo(FALLBACK_TIMEZONE)
    limit, label = dispatcher.daily_limit(now.astimezone(tz).date())
    used = dispatcher.sent_today(now)
    queue = db.list_queue()
    ready = [q for q in queue if q.status == "ready"]
    nxt = db.earliest_future_ready(now)
    due_now = [q for q in ready if q.scheduled_at <= now]
    paused = scheduler.is_paused()

    backend_chip = ("🟡 eml 乾跑(輸出 .eml 由人寄;安全模式)"
                    if SENDING_BACKEND == "eml" else "🟢 Gmail 自動寄送")
    addr_warn = ""
    if ENABLE_COMPLIANCE_FOOTER and not address_ready():
        addr_warn = ('<div class="flash err">⚠ company profile 的 sender.address '
                     '還沒填真實地址——CAN-SPAM 必填。信件 footer 目前帶著提示佔位字;'
                     'Gmail 自動寄送會被擋下。請編輯 company/*.toml。</div>')
    from ..sending.schedule import HOLIDAY_YEARS
    check_years = {now.year} | {q.scheduled_at.year for q in ready}
    missing_years = sorted(check_years - HOLIDAY_YEARS)
    if missing_years:
        addr_warn += (f'<div class="flash err">⚠ 假日表缺 {missing_years} 年資料'
                      f'(內建 {sorted(HOLIDAY_YEARS)}):排程可能踩到美國假日。'
                      '請更新 sending/schedule.py 的 _US_HOLIDAYS_RAW。</div>')

    lead_stage = {l.id: l.stage for l in db.all_leads()}
    _REPLY_CHIPS = {"followed_up": " 📩 對方已回信", "meeting_booked": " 📅 已約會議",
                    "archived": " 🚫 已退訂/歸檔"}
    rows = ""
    for q in queue:
        local = q.scheduled_at   # 儲存時即為 buyer 當地時間(帶 offset)
        overdue = (' <span class="overdue">到期</span>'
                   if q.status == "ready" and q.scheduled_at <= now else "")
        # 已寄出的信,補顯示對方後續動態(回信/退訂由偵測或 track 事件推進)
        reply_chip = ""
        if q.status == "sent":
            chip = _REPLY_CHIPS.get(lead_stage.get(q.lead_id, ""), "")
            if chip:
                reply_chip = f'<span class="chip">{chip}</span>'
        action = ""
        if q.status == "ready":
            action = (f'<form class="inline" method="post" action="/outbox/{q.id}/cancel">'
                      f'<button class="btn sec" type="submit">取消</button></form>')
        elif q.status == "failed":
            action = (f'<form class="inline" method="post" action="/outbox/{q.id}/retry">'
                      f'<button class="btn sec" type="submit">重排</button></form>')
        note = e((q.error or "")[:80]) if q.error else (q.message_id or "")
        test_chip = ' <span class="chip">🧪 測試</span>' if q.test else ""
        rows += (f"<tr><td><a href='/leads/{q.lead_id}'>{e(q.company)}</a>{test_chip}</td>"
                 f"<td>seq{q.sequence_no}</td><td>{e(q.to_email)}</td>"
                 f"<td>{local:%m/%d %H:%M}{overdue}</td>"
                 f"<td>{QUEUE_STATUS_LABELS.get(q.status, q.status)}{reply_chip}</td>"
                 f"<td><small class='hint'>{note}</small></td><td>{action}</td></tr>")
    if not rows:
        rows = ('<tr><td colspan="7">佇列是空的——到 <a href="/review">覆核佇列</a> '
                "核准信件就會排進來。</td></tr>")

    unsub_rows = "".join(
        f"<tr><td>{e(u['value'])}</td><td>{e(u['kind'])}</td>"
        f"<td>{e(u['source'] or '')}</td><td>{e((u['created_at'] or '')[:10])}</td></tr>"
        for u in db.list_unsubscribed()
    ) or '<tr><td colspan="4">(無退訂紀錄)</td></tr>'

    next_info = "—"
    if nxt:
        next_info = (f"{e(nxt.company)} seq{nxt.sequence_no} @ "
                     f"{nxt.scheduled_at:%m/%d %H:%M}(buyer 當地)")

    body = f"""
<h1>寄送佇列 <small class="hint" style="font-size:13px">(本頁每 10 秒自動更新)</small></h1>
{addr_warn}
<div id="live">
<div class="cards">
  <div class="stat"><b>{used}/{limit}</b><span>今日寄出({e(label)})</span></div>
  <div class="stat"><b>{len(ready)}</b><span>排程中</span></div>
  <div class="stat"><b>{len(due_now)}</b><span>已到期待寄</span></div>
  <div class="stat"><b>{sum(1 for q in queue if q.status == 'sent')}</b><span>累計寄出</span></div>
</div>
<div class="card">
  <div class="row">
    <span class="chip">後端:{backend_chip}</span>
    <span class="chip">排程器:{'⏸ 已暫停' if paused else '🟢 自動輪詢中(每 60 秒)'}</span>
    <span class="chip">上次檢查:{e(scheduler.last_run() or '—')}</span>
    <span class="chip">下一封:{next_info}</span>
  </div>
  <div class="row">
    <form class="inline" method="post" action="/outbox/run">
      <button class="btn" type="submit">▶ 立刻跑一輪(寄最早到期的 1 封)</button>
    </form>
    <form class="inline" method="post" action="/outbox/pause">
      <input type="hidden" name="paused" value="{'0' if paused else '1'}">
      <button class="btn {'gold' if paused else 'warn'}" type="submit">
        {'▶ 恢復自動寄送' if paused else '⏸ 暫停自動寄送'}</button>
    </form>
  </div>
  <p><small class="hint">節奏防線:一次只寄 1 封、同日排程自動錯開 60–90 分鐘、
  warmup 前 {WARMUP_WEEKS} 週每天上限壓低(之後 {DAILY_LIMIT_NORMAL} 封/天)、
  只在 buyer 當地工作日 09:30–16:30 寄、對方回信自動取消剩餘跟進。</small></p>
</div>
<div class="card">
  <h2 style="margin-top:0">🧪 三輪測試序列(走完整流程,含人工覆核)</h2>
  <p>模擬真實名單的<b>全程</b>:按下後三輪測試草稿會進<b>覆核佇列</b>,
  你像對真名單一樣檢視、改稿、按「核准整串」→ 才照壓縮時程
  <b>0 / +2 / +4 分鐘</b>(真實為 +4/+6 工作日)自動寄出,seq2/3 接同一信串。
  <b>不佔 warmup 額度、不受限流、不動漏斗</b>。</p>
  <form method="post" action="/outbox/test" class="row">
    <input name="to_email" type="email" placeholder="你的收信信箱"
           value="mike410123024@gmail.com" style="min-width:280px" required>
    <button class="btn gold" type="submit">建立測試草稿 → 前往覆核</button>
  </form>
  <p><small class="hint">驗證清單:①覆核頁三封同屏可改稿 ②核准後佇列出現三列
  ③三封都到且同一信串(Re:)④間隔約 2 分鐘 ⑤footer 完整;
  <b>進階:收到 seq1 後馬上回信</b> → seq2/3 應被自動取消(回覆煞車)。
  重按會產生新草稿、核准時舊測試排程自動作廢。</small></p>
</div>
<h2>佇列明細({len(queue)} 封)</h2>
<table><tr><th>公司</th><th>序列</th><th>收件人</th><th>排定時間(buyer 當地)</th>
<th>狀態</th><th>備註</th><th></th></tr>{rows}</table>
<h2>退訂名單 <small class="hint">(寄送前自動比對;email 或整個網域)</small></h2>
<form method="post" action="/outbox/unsub" class="row">
  <input name="value" placeholder="email 或網域(例 spam.com)" style="min-width:260px" required>
  <button class="btn sec" type="submit">加入退訂</button>
</form>
<table><tr><th>對象</th><th>類型</th><th>來源</th><th>加入日</th></tr>{unsub_rows}</table>
</div><!-- /live -->
<h2>排程器日誌 <small class="hint">(每 3 秒自動更新;回信偵測 📩 / 退訂 🚫 都在這裡)</small></h2>
<pre class="log" id="dlog">(載入中…)</pre>
<script>
async function dpoll() {{
  try {{
    const r = await fetch('/outbox/log'); const s = await r.json();
    document.getElementById('dlog').textContent = s.log.length ? s.log.join('\\n') : '(尚無紀錄)';
  }} catch (e) {{}}
  setTimeout(dpoll, 3000);
}}
dpoll();

// 全頁動態區自動刷新:統計卡/佇列/退訂名單每 10 秒更新一次;
// 使用者正在輸入時跳過本輪(避免吃掉打到一半的字)
async function lpoll() {{
  const a = document.activeElement;
  if (!(a && (a.tagName === 'INPUT' || a.tagName === 'TEXTAREA'))) {{
    try {{
      const r = await fetch(location.pathname);
      const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
      const fresh = doc.getElementById('live');
      if (fresh) document.getElementById('live').innerHTML = fresh.innerHTML;
    }} catch (e) {{}}
  }}
  setTimeout(lpoll, 10000);
}}
setTimeout(lpoll, 10000);
</script>"""
    return page("寄送佇列", body, active="/outbox", flash=msg, flash_err=err)


@app.post("/outbox/test")
def outbox_test(to_email: str = Form(...)):
    """建三輪測試草稿進覆核佇列——跟真名單走同一道人工核准閘門(覆核也是測試的一環)。"""
    from urllib.parse import quote

    from ..sending.sequence import queue_test_review

    try:
        lead = queue_test_review(to_email)
    except ValueError as exc:
        return RedirectResponse(f"/outbox?err={quote(str(exc))}", status_code=303)
    msg = (f"🧪 三輪測試草稿已進覆核佇列(收件人 {lead.email})——"
           "請像對真名單一樣逐封檢視、按「核准整串」;核准後照壓縮時程"
           "(0 / +2 / +4 分鐘)自動寄出,約 5 分鐘跑完。")
    return RedirectResponse(f"/review?msg={quote(msg)}#lead-{lead.id}", status_code=303)


@app.post("/outbox/run")
def outbox_run():
    lines = scheduler.run_one_round(manual=True)
    from urllib.parse import quote
    return RedirectResponse(f"/outbox?msg={quote(lines[-1] if lines else '完成')}",
                            status_code=303)


@app.post("/outbox/pause")
def outbox_pause(paused: str = Form("1")):
    scheduler.set_paused(paused == "1")
    return RedirectResponse("/outbox", status_code=303)


@app.post("/outbox/{qid}/cancel")
def outbox_cancel(qid: int):
    qe = db.get_queued(qid)
    if qe and qe.status == "ready":
        qe.status = "cancelled"
        qe.error = "使用者手動取消"
        db.save_queued(qe)
    return RedirectResponse("/outbox", status_code=303)


@app.post("/outbox/{qid}/retry")
def outbox_retry(qid: int):
    """失敗的信重排回佇列(狀態改 ready,下一輪到期即重寄)。"""
    qe = db.get_queued(qid)
    if qe and qe.status == "failed":
        qe.status = "ready"
        qe.error = None
        db.save_queued(qe)
    return RedirectResponse("/outbox", status_code=303)


@app.post("/outbox/unsub")
def outbox_unsub(value: str = Form(...)):
    value = value.strip().lower()
    kind = "email" if "@" in value else "domain"
    db.add_unsubscribe(value, kind=kind, source="manual")
    # 立刻取消佇列中所有命中退訂的 ready 信
    n = 0
    for q in db.list_queue(status="ready"):
        if db.is_unsubscribed(q.to_email):
            q.status = "cancelled"
            q.error = "已加入退訂名單"
            db.save_queued(q)
            n += 1
    from urllib.parse import quote
    return RedirectResponse(
        f"/outbox?msg={quote(f'已加入退訂:{value}(取消 {n} 封排程中信件)')}",
        status_code=303)


@app.get("/outbox/log")
def outbox_log():
    return JSONResponse({"log": scheduler.log_lines(),
                         "last_run": scheduler.last_run(),
                         "paused": scheduler.is_paused()})


# ─────────────────────────── 匯入 ───────────────────────────

_TIER_OPTIONS = """
    <option value="T1_coffee">Tier:T1 咖啡通路</option>
    <option value="T0_rep">T0 Rep Group</option>
    <option value="T2_kitchen">T2 廚房專賣</option>
    <option value="T3_mass">T3 大型量販(會被自動歸檔)</option>"""


@app.get("/import", response_class=HTMLResponse)
def import_form():
    source_opts = "".join(
        f'<option value="{s}">{label}</option>'
        for s, label in SOURCE_LABELS.items() if s not in ("places", "ocr")
    )
    body = f"""
<h1>匯入名單</h1>
<div class="card">
<h2 style="margin-top:0">① CSV 上傳</h2>
<p>支援 <b>Apollo 網頁匯出檔</b>(欄位自動識別、姓名自動合併)與簡易格式。
選對「來源標籤」——名單頁會顯示,pipeline 也能依來源篩選。</p>
<form method="post" action="/import" enctype="multipart/form-data" class="row">
  <input type="file" name="file" accept=".csv" required>
  <select name="label">{source_opts}</select>
  <select name="tier">{_TIER_OPTIONS}</select>
  <button class="btn" type="submit">匯入</button>
</form></div>
<div class="card">
<h2 style="margin-top:0">② Google 地圖掃描城市店家</h2>
<p>用一句自然語言搜尋(「業態 in 城市, 州」),Google 回傳最多 20 家實體店
(店名/網站/州,<b>不含聯絡人 email</b>——之後用 Hunter 或 Apollo 補)。
搜尋句自己改,要掃哪個城市、什麼業態,你說了算。</p>
<form method="post" action="/scan" class="row">
  <input name="query" value="specialty coffee roaster in Chicago, IL"
         style="min-width:340px" required>
  <select name="tier">{_TIER_OPTIONS}</select>
  <button class="btn" type="submit">掃描並入庫</button>
</form>
<p><small class="hint">例:kitchenware store in Chicago, IL /
coffee shop in Naperville, IL / specialty coffee roaster in Austin, TX</small></p>
</div>"""
    return page("匯入名單", body, active="/import")


# 待確認的匯入批次(記憶體暫存:單人系統,預覽 → 確認之間的中繼站)
_PENDING_IMPORTS: dict[str, dict] = {}
_PENDING_CAP = 5  # 只留最近幾批,舊的自動淘汰


def _prepare_import(raw: list, source_label: str | None = None) -> tuple[list, str]:
    """去重 + 過濾庫內既有,「不入庫」。回傳 (待確認名單, 去重日誌)。"""
    if source_label:
        for r in raw:
            r.source = source_label
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        merged = dedupe(raw, verbose=True)
        kept = []
        for r in merged:
            if db.find_by_company_or_email(r.company, r.email):
                print(f"  ✂ 略過「{r.company}」—— 資料庫已有此公司")
            else:
                kept.append(r)
    return kept, buffer.getvalue().strip() or "(無去重紀錄)"


def _preview_page(title: str, token: str, raws: list, total: int, log_text: str) -> HTMLResponse:
    """匯入預覽:逐筆勾選(預設全選),取消勾選 = 不匯入;確認後才寫入資料庫。"""
    rows = "".join(
        f'<tr><td><input type="checkbox" name="keep" value="{i}" checked></td>'
        f"<td>{e(r.company)}</td>"
        f"<td>{e(r.contact_name or '—')}<br><small class='hint'>{e(r.title or '')}</small></td>"
        f"<td>{e(r.email or '—')}</td>"
        f"<td>{e(r.state or '?')}</td>"
        f'<td><span class="chip">{e(r.tier)}</span></td>'
        f"<td>{len(r.alt_contacts) or '—'}</td></tr>"
        for i, r in enumerate(raws)
    ) or '<tr><td colspan="7">去重後沒有可匯入的新名單。</td></tr>'
    body = f"""
<h1>檢視後確認匯入 — {e(title)}</h1>
<div class="cards">
  <div class="stat"><b>{total}</b><span>原始筆數</span></div>
  <div class="stat"><b>{len(raws)}</b><span>待你確認</span></div>
  <div class="stat"><b>{total - len(raws)}</b><span>去重/已在庫</span></div>
</div>
<div class="card">
<p><b>尚未寫入資料庫。</b>取消勾選 = 不匯入該筆;確認後才正式入庫。</p>
<form method="post" action="/import/confirm">
  <input type="hidden" name="token" value="{token}">
  <table>
    <tr><th><input type="checkbox" checked
        onclick="document.querySelectorAll('input[name=keep]').forEach(c=>c.checked=this.checked)">
        </th><th>公司</th><th>聯絡人</th><th>email</th><th>州</th><th>Tier</th><th>備選</th></tr>
    {rows}
  </table>
  <div class="row" style="margin-top:12px">
    <button class="btn" type="submit" {"disabled" if not raws else ""}>✅ 確認匯入勾選的名單</button>
    <a class="btn sec" href="/import">✘ 放棄這批</a>
  </div>
</form></div>
<h2>去重明細</h2><pre class="log">{e(log_text)}</pre>"""
    return page("確認匯入", body, active="/import")


def _stash_pending(title: str, raws: list, total: int, log_text: str) -> HTMLResponse:
    import secrets

    token = secrets.token_hex(8)
    _PENDING_IMPORTS[token] = {"title": title, "raws": raws,
                               "total": total, "log": log_text}
    while len(_PENDING_IMPORTS) > _PENDING_CAP:  # 淘汰最舊
        _PENDING_IMPORTS.pop(next(iter(_PENDING_IMPORTS)))
    return _preview_page(title, token, raws, total, log_text)


@app.post("/import", response_class=HTMLResponse)
async def import_csv(file: UploadFile, tier: str = Form("T1_coffee"),
                     label: str = Form("manual")):
    db.init_db()
    IMPORTS_DIR.mkdir(exist_ok=True)
    dest = IMPORTS_DIR / f"web_{datetime.now():%Y%m%d_%H%M%S}_{Path(file.filename or 'upload.csv').name}"
    dest.write_bytes(await file.read())

    raw = ManualAdapter().fetch(file=str(dest), tier=tier)
    kept, log_text = _prepare_import(raw, source_label=label)
    return _stash_pending(f"CSV:{Path(file.filename or '').name}", kept, len(raw), log_text)


@app.post("/scan", response_class=HTMLResponse)
def scan_places(query: str = Form(...), tier: str = Form("T1_coffee")):
    from ..adapters import PlacesAdapter

    db.init_db()
    raw = PlacesAdapter().fetch(query=query, tier=tier)
    kept, log_text = _prepare_import(raw)  # source 保持 places
    return _stash_pending(f"掃描:{query}", kept, len(raw), log_text)


@app.post("/import/confirm", response_class=HTMLResponse)
def import_confirm(token: str = Form(...), keep: list[str] = Form(default=[])):
    pending = _PENDING_IMPORTS.pop(token, None)
    if pending is None:
        return page("批次已失效", "<h1>這批預覽已失效</h1><p>請重新上傳/掃描。</p>"
                    '<p><a class="btn" href="/import">← 回匯入頁</a></p>', active="/import")
    raws = pending["raws"]
    chosen = [raws[int(i)] for i in keep if i.isdigit() and int(i) < len(raws)]
    for r in chosen:
        db.save_lead(to_lead(r))
    dropped = len(raws) - len(chosen)
    body = f"""
<h1>匯入完成 — {e(pending["title"])}</h1>
<div class="cards">
  <div class="stat"><b>{len(chosen)}</b><span>已入庫</span></div>
  <div class="stat"><b>{dropped}</b><span>檢視時被你刪除</span></div>
  <div class="stat"><b>{pending["total"] - len(raws)}</b><span>去重/已在庫</span></div>
</div>
<div class="row">
  <a class="btn" href="/pipeline">▶ 下一步:執行 Pipeline</a>
  <a class="btn sec" href="/leads?stage=new">查看新名單</a>
  <a class="btn sec" href="/import">← 再匯一批</a>
</div>"""
    return page("匯入完成", body, active="/import")


# ─────────────────────────── Pipeline ───────────────────────────

@app.get("/pipeline", response_class=HTMLResponse)
def pipeline_page():
    snap = jobs.snapshot()
    pending_new = [l for l in db.list_leads(stage="new") if not l.pending_draft]
    running = snap["running"]

    # 州與來源選項:從庫內現有新名單動態產生,附各自筆數
    from collections import Counter
    state_counts = Counter((l.state or "?") for l in pending_new)
    source_counts = Counter(l.source for l in pending_new)
    state_opts = '<option value="">全部州</option>' + "".join(
        f'<option value="{e(s)}">{e(s)}({n} 筆)</option>'
        for s, n in sorted(state_counts.items())
    )
    source_opts = '<option value="">全部來源</option>' + "".join(
        f'<option value="{e(s)}">{e(SOURCE_LABELS.get(s, s))}({n} 筆)</option>'
        for s, n in sorted(source_counts.items())
    )
    body = f"""
<h1>Pipeline(豐富 → 評分 → 寫信 → 覆核佇列)</h1>
<div class="card">
<p>待處理新名單:<b>{len(pending_new)}</b> 筆。每筆約 2–4 分鐘(會上網查該公司背景),
平行處理;啟動後可離開此頁,隨時回來看進度。</p>
<form method="post" action="/pipeline/start" class="row">
  <label>州 <select name="state">{state_opts}</select></label>
  <label>來源 <select name="source">{source_opts}</select></label>
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
def pipeline_start(limit: str = Form(""), workers: int = Form(3),
                   state: str = Form(""), source: str = Form("")):
    from ..graph import prepare_batch, run_pipeline

    limit_n = int(limit) if limit.strip().isdigit() else None
    workers = max(1, min(int(workers), 5))

    def job(log):
        leads, messages = prepare_batch(
            limit_n, state=state or None, source=source or None)
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


# ─────────────────────────── 清空資料(雙重確認) ───────────────────────────

@app.get("/wipe", response_class=HTMLResponse)
def wipe_confirm(err: str = ""):
    from ..config import OUTBOX_DIR

    leads = db.all_leads()
    eml_count = len(list(OUTBOX_DIR.glob("*.eml"))) if OUTBOX_DIR.exists() else 0
    running = jobs.snapshot()["running"]
    flash_err = ""
    if err == "confirm":
        flash_err = "確認字不符——必須輸入大寫 DELETE 才會執行"
    elif err == "running":
        flash_err = "背景任務執行中,禁止清空;等任務結束再操作"

    body = f"""
<h1 style="color:var(--red)">⚠️ 清空全部資料</h1>
<div class="card" style="border-color:#DBB">
<p>此操作<b>無法復原</b>,將刪除:</p>
<ul>
  <li><b>{len(leads)} 筆名單</b>(含評分、背景情報、信件草稿、互動紀錄、階段狀態)</li>
  <li>寄送佇列(排程中/已寄出的紀錄)</li>
  <li>Pipeline 斷點快取(checkpoints)</li>
  <li>outbox 裡 {eml_count} 封已核准信件檔</li>
</ul>
<p>會保留:<code>imports/</code> 裡你上傳過的 CSV 原檔(可重新匯入)、
<b>退訂名單</b>(合規承諾,清掉會導致重寄給已退訂的人)。</p>
<form method="post" action="/wipe" class="row">
  <input name="confirm" placeholder="輸入 DELETE 以確認" autocomplete="off"
         style="min-width:220px" {"disabled" if running else ""}>
  <button class="btn warn" type="submit" {"disabled" if running else ""}>
    {"背景任務執行中,暫不可清空" if running else "確認清空(無法復原)"}</button>
  <a class="btn sec" href="/">取消</a>
</form></div>"""
    return page("清空資料", body, active="/", flash_err=flash_err)


@app.post("/wipe")
def wipe_execute(confirm: str = Form("")):
    from ..config import CHECKPOINT_PATH, OUTBOX_DIR

    if jobs.snapshot()["running"]:
        return RedirectResponse("/wipe?err=running", status_code=303)
    if confirm.strip() != "DELETE":   # 大小寫都要正確,防誤觸
        return RedirectResponse("/wipe?err=confirm", status_code=303)

    deleted = db.wipe_leads()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{CHECKPOINT_PATH}{suffix}").unlink(missing_ok=True)
    if OUTBOX_DIR.exists():
        for f in OUTBOX_DIR.glob("*.eml"):
            f.unlink()
    return RedirectResponse(f"/?wiped={deleted}", status_code=303)


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
