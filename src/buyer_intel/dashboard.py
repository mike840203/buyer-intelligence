"""Pipeline 看板:靜態 HTML dashboard。

漏斗視圖(已接觸 → 已跟進 → 樣品寄出 → 報價中 → PO)+ 逾期未跟進警示。
執行 buyer-intel dashboard 產出 dashboard.html,瀏覽器開啟即可。
"""

from __future__ import annotations

from datetime import date

from . import db
from .company import get_company
from .config import DASHBOARD_PATH
from .models import Lead

FUNNEL_ORDER = [
    ("new", "新名單"),
    ("contacted", "已觸達"),
    ("meeting_booked", "已約會議"),
    ("met_at_show", "展中接觸"),
    ("followed_up", "已跟進"),
    ("sample_sent", "樣品寄出"),
    ("quoting", "報價中"),
    ("po_received", "PO 到手"),
]


def _lead_row(lead: Lead) -> str:
    due = lead.next_action_due.isoformat() if lead.next_action_due else "—"
    return (
        f"<tr><td>{lead.company}</td><td>{lead.contact_name or ''}</td>"
        f"<td>{lead.tier}</td><td>{lead.region}</td>"
        f"<td>{lead.grade or '—'}</td><td>{lead.stage}</td><td>{due}</td></tr>"
    )


def render() -> str:
    leads = db.all_leads()
    active = [l for l in leads if l.stage != "archived"]
    overdue = db.overdue_leads()

    funnel_cells = "".join(
        f"<div class='cell'><b>{sum(1 for l in active if l.stage == stage)}</b>"
        f"<span>{label}</span></div>"
        for stage, label in FUNNEL_ORDER
    )
    overdue_rows = "".join(_lead_row(l) for l in overdue) or \
        "<tr><td colspan='7'>無逾期項目 🎉</td></tr>"
    all_rows = "".join(
        _lead_row(l) for l in sorted(active, key=lambda x: (x.grade or "Z", x.company))
    ) or "<tr><td colspan='7'>資料庫為空,先執行 ingest。</td></tr>"

    company = get_company().name
    return f"""<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>{company} Buyer Pipeline</title>
<style>
body{{font-family:sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#22261F}}
h1{{color:#143D30}} h2{{margin-top:36px}}
.funnel{{display:flex;gap:8px;flex-wrap:wrap}}
.cell{{background:#fff;border:1px solid #DDDACF;padding:14px 18px;min-width:96px;text-align:center;border-radius:6px}}
.cell b{{display:block;font-size:26px;color:#1F5A46}} .cell span{{font-size:12px;color:#7C8178}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th{{background:#143D30;color:#fff;text-align:left;padding:8px 12px}}
td{{padding:8px 12px;border-bottom:1px solid #DDDACF}}
.warn th{{background:#8A3B22}}
</style></head><body>
<h1>{company} Buyer Pipeline — {date.today().isoformat()}</h1>
<div class="funnel">{funnel_cells}</div>
<h2>⚠️ 逾期未跟進({len(overdue)})</h2>
<table class="warn"><tr><th>公司</th><th>聯絡人</th><th>Tier</th><th>地區</th>
<th>分級</th><th>階段</th><th>下次行動</th></tr>{overdue_rows}</table>
<h2>全部名單({len(active)} 筆,已排除歸檔)</h2>
<table><tr><th>公司</th><th>聯絡人</th><th>Tier</th><th>地區</th>
<th>分級</th><th>階段</th><th>下次行動</th></tr>{all_rows}</table>
</body></html>"""


def write_dashboard() -> str:
    html = render()
    DASHBOARD_PATH.write_text(html, encoding="utf-8")
    return str(DASHBOARD_PATH)
