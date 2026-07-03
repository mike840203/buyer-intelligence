"""L2 去重與地區映射的單元測試(不呼叫 API)。"""

from buyer_intel.enrich import dedupe, map_region
from buyer_intel.models import RawLead


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


def test_dedupe_keeps_richer_record():
    leads = [
        RawLead(company="Clive Coffee"),
        RawLead(company="Clive Coffee", email="buyer@clivecoffee.com",
                contact_name="Jane Doe", title="Buyer"),
    ]
    result = dedupe(leads)
    assert len(result) == 1
    assert result[0].email == "buyer@clivecoffee.com"
