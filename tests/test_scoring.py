"""L3 規則評分的單元測試(不呼叫 API)。"""

from buyer_intel.scoring import (
    grade_of,
    rule_authority_score,
    rule_region_score,
    rule_size_score,
)


def test_size_sweet_spot():
    # 甜蜜點 5–100 家門市拿滿分
    assert rule_size_score(5) == 100.0
    assert rule_size_score(100) == 100.0
    # 過大反而扣分(第一年接不住 Costco 級訂單)
    assert rule_size_score(600) < rule_size_score(50)
    # 未知給中性分
    assert rule_size_score(None) == 50.0


def test_region_priority():
    # P1(PNW、TX)> P2(CA、NY)> P3(MIDWEST)> 其他
    assert rule_region_score("PNW") == rule_region_score("TX")
    assert rule_region_score("PNW") > rule_region_score("CA")
    assert rule_region_score("CA") > rule_region_score("MIDWEST")
    assert rule_region_score("MIDWEST") > rule_region_score("OTHER")


def test_authority_keywords():
    assert rule_authority_score("Owner") > rule_authority_score("Sales Associate")
    assert rule_authority_score("Category Manager") >= 90
    assert rule_authority_score(None) == 30.0


def test_grade_thresholds():
    assert grade_of(70) == "A"
    assert grade_of(69.9) == "B"
    assert grade_of(50) == "B"
    assert grade_of(49.9) == "C"
