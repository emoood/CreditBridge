from decimal import Decimal

from backend.app.services import policy_rules as pr

P = pr.Policy.load()


def test_policy_loads_with_source():
    assert P.source_url.startswith("https://www.aud.edu")
    assert P.max_transfer_fraction == Decimal("0.5")


def test_min_level():
    assert pr.check_min_level(P, pr.CourseFacts(target_level=100)).status == pr.PASS
    assert pr.check_min_level(P, pr.CourseFacts(target_level=50)).status == pr.FAIL
    assert pr.check_min_level(P, pr.CourseFacts()).status == pr.REVIEW


def test_grade_lower_band():
    f = lambda g, lvl=200: pr.CourseFacts(source_grade=g, target_level=lvl)
    assert pr.check_grade_lower(P, f("C")).status == pr.PASS
    assert pr.check_grade_lower(P, f("C-")).status == pr.FAIL
    assert pr.check_grade_lower(P, f("A+")).status == pr.PASS
    assert pr.check_grade_lower(P, f("B", 300)).status == pr.NA


def test_grade_upper_band():
    f = lambda g, lvl=300: pr.CourseFacts(source_grade=g, target_level=lvl)
    assert pr.check_grade_upper(P, f("B")).status == pr.PASS
    assert pr.check_grade_upper(P, f("B-")).status == pr.FAIL
    assert pr.check_grade_upper(P, f("C", 400)).status == pr.FAIL
    assert pr.check_grade_upper(P, f("C", 200)).status == pr.NA


def test_missing_or_unknown_grade_is_review_not_pass():
    assert pr.check_grade_upper(P, pr.CourseFacts(target_level=300)).status == pr.REVIEW
    assert pr.check_grade_upper(P, pr.CourseFacts(source_grade="Z", target_level=300)).status == pr.REVIEW


def test_restricted_types():
    assert pr.check_restricted(P, pr.CourseFacts(target_restricted_type="capstone", target_type_verified=True)).status == pr.FAIL
    assert pr.check_restricted(P, pr.CourseFacts(target_restricted_type=None, target_type_verified=True)).status == pr.PASS
    assert pr.check_restricted(P, pr.CourseFacts(target_restricted_type="capstone")).status == pr.REVIEW


def test_aggregate_cap_uses_decimal():
    D = Decimal
    assert pr.check_aggregate_cap(P, D("120"), D("57"), D("3")).status == pr.PASS  # exactly 60
    assert pr.check_aggregate_cap(P, D("120"), D("57"), D("3.5")).status == pr.FAIL
    assert pr.check_aggregate_cap(P, D("120"), None, D("3")).status == pr.REVIEW


def test_evaluate_course_includes_static_flags_and_hard_fail():
    r = pr.evaluate_course(P, pr.CourseFacts("D", 300, "capstone", True))
    ids = {x.rule_id for x in r}
    assert {"AUD_MIN_LEVEL", "AUD_GRADE_UPPER", "AUD_RESTRICTED_COURSE_TYPES", "AUD_INSTITUTION_RECOGNITION"} <= ids
    assert pr.has_hard_fail(r)
