"""信末 footer:簽名 + CAN-SPAM 合規段(純文字,依 company profile 組裝)。

美國 CAN-SPAM 對商業信要求兩件事:(1) 寄件人真實實體地址、(2) 可運作的退訂機制。
Gmail 無法部署退訂網頁,故用 reply UNSUBSCRIBE(CAN-SPAM 接受、即時可識別)。

address 尚未填真實值時,footer 會插入明顯的 ⚠ 提示,讓人在覆核時就看到、
並讓 dispatcher 有依據擋下自動寄送(見 dispatcher.py)。
"""

from __future__ import annotations

from ..company import CompanyProfile, get_company

_ADDRESS_MISSING = "[⚠ POSTAL ADDRESS NOT SET — edit company profile before sending]"


def signature(company: CompanyProfile) -> str:
    """純簽名(footer 關閉時只附這段)。"""
    s = company.sender
    lines = ["--", s.name or company.name, company.name]
    contact = " | ".join(x for x in (s.email, company.website) if x)
    if contact:
        lines.append(contact)
    return "\n".join(lines)


def compliance_footer(company: CompanyProfile) -> str:
    """CAN-SPAM 合規段:寄件人資訊 + 實體地址 + reply 退訂。"""
    s = company.sender
    address = s.address if s.address_ready else _ADDRESS_MISSING
    id_lines = [x for x in (s.name or company.name, s.title, company.name, address) if x]
    contact = " | ".join(x for x in (s.phone, s.email, company.website) if x)
    if contact:
        id_lines.append(contact)
    notice = (
        f"You're receiving this one-to-one B2B email from {company.name}, "
        "a Taiwan-based manufacturer. Our postal address is listed above. "
        'If you\'d rather not hear from us, reply with "UNSUBSCRIBE" in the '
        "subject or body and we'll remove you within 10 business days."
    )
    return "--\n" + "\n".join(id_lines) + "\n\n" + notice


def append_footer(body: str, enable_footer: bool | None = None,
                  company: CompanyProfile | None = None) -> str:
    """把 footer 接到信件內文末端。enable_footer 省略時讀 config。"""
    company = company or get_company()
    if enable_footer is None:
        from ..config import ENABLE_COMPLIANCE_FOOTER
        enable_footer = ENABLE_COMPLIANCE_FOOTER
    foot = compliance_footer(company) if enable_footer else signature(company)
    return body.rstrip() + "\n\n" + foot


def address_ready(company: CompanyProfile | None = None) -> bool:
    """dispatcher 寄送前檢查:合規地址是否已填。"""
    return (company or get_company()).sender.address_ready
