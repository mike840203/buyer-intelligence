"""展中手機 Web UI(FastAPI):拍名片 → OCR 入庫 → 秒回 company brief。

啟動:buyer-intel serve(預設 0.0.0.0:8000,手機連同一 Wi-Fi 開瀏覽器即可)
流程:
1. 首頁上傳名片照片
2. OCR 抽取 → 比對既有名單(預約客戶 or 新接觸)→ 入庫
3. 回傳 company brief 頁,附會談紀錄表單
4. 談完後在同頁記會談重點,直接掛到該 lead 上
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import FastAPI, Form, UploadFile
from fastapi.responses import HTMLResponse

from .. import db
from ..models import Interaction, Lead
from .brief import company_brief
from .ocr import extract_card

app = FastAPI(title="Ankomn Field Ops")

_PAGE = """<!DOCTYPE html><html lang="zh-Hant"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ankomn Field Ops</title>
<style>
body{{font-family:sans-serif;max-width:640px;margin:0 auto;padding:16px;line-height:1.6}}
h1{{font-size:20px}} textarea,input[type=file]{{width:100%}}
button{{padding:10px 20px;font-size:16px;background:#1F5A46;color:#fff;border:0;border-radius:6px}}
pre{{white-space:pre-wrap;background:#f5f4ef;padding:12px;border-radius:6px}}
.tag{{display:inline-block;background:#eee;border-radius:4px;padding:2px 8px;font-size:13px;margin-right:6px}}
</style></head><body>{body}</body></html>"""


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    body = (
        "<h1>📇 名片掃描</h1>"
        "<form action='/card' method='post' enctype='multipart/form-data'>"
        "<input type='file' name='photo' accept='image/*' capture='environment' required>"
        "<p><button type='submit'>抽取並產生 brief</button></p></form>"
        "<p><a href='/leads'>今日接觸清單</a></p>"
    )
    return _PAGE.format(body=body)


@app.post("/card", response_class=HTMLResponse)
async def scan_card(photo: UploadFile) -> str:
    contact = extract_card(
        await photo.read(), photo.content_type or "image/jpeg"
    )
    existing = db.find_by_company_or_email(contact.company, contact.email)
    if existing:
        lead = existing
        status = "✅ 名單內既有接觸(可能是預約客戶)"
        # 補全名片上較新的聯絡資訊
        lead.contact_name = contact.contact_name or lead.contact_name
        lead.title = contact.title or lead.title
        lead.email = contact.email or lead.email
    else:
        lead = Lead(
            company=contact.company,
            contact_name=contact.contact_name,
            title=contact.title,
            email=contact.email,
            city=contact.city,
            state=contact.state,
            source="ocr",
            stage="met_at_show",
        )
        status = "🆕 新接觸,已建檔"
    lead.stage = "met_at_show"
    # same-day follow-up 紀律:24 小時內必須跟進
    lead.next_action_due = date.today() + timedelta(days=1)
    lead = db.save_lead(lead)

    brief = company_brief(lead)
    body = (
        f"<h1>{lead.company}</h1>"
        f"<p><span class='tag'>{status}</span>"
        f"<span class='tag'>{lead.tier}</span>"
        f"<span class='tag'>{lead.grade or '未評分'}</span></p>"
        f"<pre>{brief}</pre>"
        f"<h2>會談重點</h2>"
        f"<form action='/note/{lead.id}' method='post'>"
        "<textarea name='note' rows='5' placeholder='談了什麼、對方要什麼、下一步'></textarea>"
        "<p><button type='submit'>存檔</button></p></form>"
        "<p><a href='/'>← 掃下一張</a></p>"
    )
    return _PAGE.format(body=body)


@app.post("/note/{lead_id}", response_class=HTMLResponse)
def add_note(lead_id: int, note: str = Form(...)) -> str:
    lead = db.get_lead(lead_id)
    if lead is None:
        return _PAGE.format(body="<p>找不到該 lead。</p><p><a href='/'>← 回首頁</a></p>")
    lead.interactions.append(Interaction(kind="meeting_note", content=note))
    db.save_lead(lead)
    body = f"<p>✅ 已記錄到 <b>{lead.company}</b>。</p><p><a href='/'>← 掃下一張</a></p>"
    return _PAGE.format(body=body)


@app.get("/leads", response_class=HTMLResponse)
def today_leads() -> str:
    rows = "".join(
        f"<li><b>{l.company}</b> — {l.contact_name or ''}({l.title or ''})"
        f" <span class='tag'>{l.stage}</span></li>"
        for l in db.list_leads(stage="met_at_show")
    ) or "<li>(尚無)</li>"
    return _PAGE.format(body=f"<h1>展中接觸</h1><ul>{rows}</ul><p><a href='/'>← 回首頁</a></p>")
