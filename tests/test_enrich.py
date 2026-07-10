"""L2 去重與地區映射的單元測試(不呼叫 API)。"""

from buyer_intel.enrich import _domain_from_website, dedupe, map_region
from buyer_intel.models import RawLead


def test_domain_from_website():
    assert _domain_from_website("https://www.darkmattercoffee.com/") == "darkmattercoffee.com"
    assert _domain_from_website("http://gaslightcoffee.com") == "gaslightcoffee.com"
    assert _domain_from_website("https://shop.example.com/products") == "shop.example.com"
    assert _domain_from_website(None) is None
    assert _domain_from_website("") is None


def test_map_region():
    assert map_region("WA") == "PNW"
    assert map_region("or") == "PNW"
    assert map_region("TX") == "TX"
    assert map_region("IL") == "MIDWEST"
    assert map_region("FL") == "OTHER"
    assert map_region(None) == "OTHER"


def test_dedupe_by_fuzzy_company_name():
    leads = [
        RawLead(company="Prima Coffee Equipment"),
        RawLead(company="Prima Coffee Equipment, Inc."),
        RawLead(company="Seattle Coffee Gear"),
    ]
    result = dedupe(leads)
    assert len(result) == 2


def test_dedupe_by_email_domain():
    leads = [
        RawLead(company="Prima Coffee", email="buyer@primacoffee.com"),
        RawLead(company="Prima Equipment LLC", email="owner@primacoffee.com"),
    ]
    assert len(dedupe(leads)) == 1


def test_freemail_domains_not_merged():
    """兩家不同小店都用 gmail,不能因同網域被誤判為同一家。"""
    leads = [
        RawLead(company="Alpha Coffee", email="alphacoffee@gmail.com"),
        RawLead(company="Beta Roasters", email="betaroast@gmail.com"),
    ]
    assert len(dedupe(leads)) == 2


def test_corporate_domain_still_merges():
    """公司網域相同仍視為同一家(即使公司名寫法差很多)。"""
    leads = [
        RawLead(company="Prima Coffee", email="jane@primacoffee.com"),
        RawLead(company="PCE Wholesale Division", email="bob@primacoffee.com"),
    ]
    assert len(dedupe(leads)) == 1


def test_dedupe_keeps_owner_over_unknown_title():
    """Reprise 真實案例:Founder/Owner 必須贏過職稱不明的同事。"""
    leads = [
        RawLead(company="Reprise Coffee Roasters", contact_name="Kati Paiz",
                email="kati@reprisecoffee.com", website="https://reprise.com"),
        RawLead(company="Reprise Coffee Roasters", contact_name="Adam Paronto",
                title="Founder, Owner", email="adam@reprisecoffee.com"),
    ]
    result = dedupe(leads)
    assert len(result) == 1
    assert result[0].contact_name == "Adam Paronto"
    # 落選者結構化保留為備選聯絡人,不丟棄
    assert [a.contact_name for a in result[0].alt_contacts] == ["Kati Paiz"]


def test_dedupe_prefers_category_buyer():
    """PersonalizationMall 真實案例:品類對口 Buyer 贏過泛職稱。"""
    leads = [
        RawLead(company="PersonalizationMall.com", contact_name="Jennifer Harris",
                title="Buyer", email="j@pmall.com", website="https://pmall.com"),
        RawLead(company="PersonalizationMall.com", contact_name="Carmen Turner",
                title="Buyer- Hardgoods, Home Decor, Seasonal", email="c@pmall.com"),
    ]
    result = dedupe(leads)
    assert len(result) == 1
    assert result[0].contact_name == "Carmen Turner"
    assert [a.contact_name for a in result[0].alt_contacts] == ["Jennifer Harris"]
    assert result[0].alt_contacts[0].email == "j@pmall.com"  # email 完整保留


def test_dedupe_preserves_every_contact():
    """三位同公司聯絡人:主收件人 1 位 + 備選 2 位,一個都不能少。"""
    leads = [
        RawLead(company="American Metalcraft", contact_name="A", title="Buyer",
                email="a@amnow.com"),
        RawLead(company="American Metalcraft", contact_name="B", title="Buyer",
                email="b@amnow.com"),
        RawLead(company="American Metalcraft", contact_name="C",
                title="Category Manager", email="c@amnow.com"),
    ]
    result = dedupe(leads)
    assert len(result) == 1
    total = 1 + len(result[0].alt_contacts)
    assert total == 3


def test_dedupe_keeps_richer_record():
    leads = [
        RawLead(company="Clive Coffee"),
        RawLead(company="Clive Coffee", email="buyer@clivecoffee.com",
                contact_name="Jane Doe", title="Buyer"),
    ]
    result = dedupe(leads)
    assert len(result) == 1
    assert result[0].email == "buyer@clivecoffee.com"
