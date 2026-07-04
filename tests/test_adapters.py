"""ManualAdapter 格式自動識別的單元測試(不呼叫 API)。"""

from buyer_intel.adapters import ManualAdapter

APOLLO_CSV = """First Name,Last Name,Title,Company,Email,Email Status,Website,City,State,Country,Industry,# Employees
Jane,Doe,Purchasing Manager,Prima Coffee Equipment,jane@primacoffee.com,Verified,https://prima-coffee.com,Louisville,Kentucky,United States,Retail,15
John,Smith,Owner,Visions Espresso,john@visions.com,Verified,https://visionsespresso.com,Seattle,Washington,United States,Retail,8
"""

SIMPLE_CSV = """company,contact_name,title,email,website,city,state,tier
Clive Coffee,Amy Lee,Buyer,amy@clive.com,https://clivecoffee.com,Portland,OR,T1_coffee
"""


def test_apollo_export_format(tmp_path):
    f = tmp_path / "apollo.csv"
    f.write_text(APOLLO_CSV, encoding="utf-8")
    leads = ManualAdapter().fetch(file=str(f), tier="T1_coffee")

    assert len(leads) == 2
    jane = leads[0]
    assert jane.company == "Prima Coffee Equipment"
    assert jane.contact_name == "Jane Doe"          # First + Last 自動合併
    assert jane.title == "Purchasing Manager"
    assert jane.email == "jane@primacoffee.com"
    assert jane.state == "Kentucky"
    assert jane.tier == "T1_coffee"                 # CSV 無 tier 欄 → 用參數預設
    assert "產業:Retail" in (jane.notes or "")
    assert "員工數:15" in (jane.notes or "")


def test_simple_format_still_works(tmp_path):
    f = tmp_path / "simple.csv"
    f.write_text(SIMPLE_CSV, encoding="utf-8")
    leads = ManualAdapter().fetch(file=str(f))

    assert len(leads) == 1
    assert leads[0].contact_name == "Amy Lee"
    assert leads[0].tier == "T1_coffee"
    assert leads[0].state == "OR"


def test_tier_param_as_default(tmp_path):
    f = tmp_path / "reps.csv"
    f.write_text("Company,First Name,Last Name\nAcme Rep Group,Bob,Chan\n", encoding="utf-8")
    leads = ManualAdapter().fetch(file=str(f), tier="T0_rep")
    assert leads[0].tier == "T0_rep"
    assert leads[0].contact_name == "Bob Chan"
