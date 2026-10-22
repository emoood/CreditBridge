"""Deterministic policy engine. No LLM, no I/O. Missing data => NEEDS_REVIEW, never PASS."""
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

POLICY_PATH = Path(__file__).resolve().parents[3] / "policies" / "aud_undergrad_demo_2026_10_22.yaml"
PASS, FAIL, REVIEW, NA = "PASS", "FAIL", "NEEDS_REVIEW", "NOT_APPLICABLE"


@dataclass
class Policy:
    policy_version_id: str
    source_url: str
    min_level: int
    min_grade_lower: str
    min_grade_upper: str
    max_transfer_fraction: Decimal
    restricted_types: list[str]
    grade_scale: list[str]

    @classmethod
    def load(cls, path: Path = POLICY_PATH) -> "Policy":
        d = yaml.safe_load(path.read_text())
        return cls(d["policy_version_id"], d["source_url"], d["min_level"], d["min_grade_lower"],
                   d["min_grade_upper"], Decimal(str(d["max_transfer_fraction"])),
                   d["restricted_types"], d["grade_scale"])


@dataclass
class CourseFacts:
    source_grade: str | None = None
    target_level: int | None = None  # AUD-equivalent level, e.g. 100, 200, 300, 400
    target_restricted_type: str | None = None  # verified type; None means unknown
    target_type_verified: bool = False


@dataclass
class RuleResult:
    rule_id: str
    status: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    policy_version_id: str = ""
    policy_source_url: str = ""
    human_review_required: bool = True


def _res(p: Policy, rule_id, status, reason, **evidence) -> RuleResult:
    return RuleResult(rule_id, status, reason, evidence, p.policy_version_id, p.source_url, True)


def _grade_meets(p: Policy, grade: str, minimum: str) -> bool | None:
    scale = p.grade_scale
    g = grade.strip().upper()
    if g not in scale:
        return None
    return scale.index(g) <= scale.index(minimum)


def check_min_level(p: Policy, f: CourseFacts) -> RuleResult:
    if f.target_level is None:
        return _res(p, "AUD_MIN_LEVEL", REVIEW, "Target level unknown.")
    ok = f.target_level >= p.min_level
    return _res(p, "AUD_MIN_LEVEL", PASS if ok else FAIL,
                f"Target level {f.target_level} {'meets' if ok else 'is below'} minimum {p.min_level}.",
                target_level=f.target_level)


def _grade_rule(p: Policy, f: CourseFacts, rule_id: str, levels: range, minimum: str) -> RuleResult:
    if f.target_level is None or f.source_grade is None:
        return _res(p, rule_id, REVIEW, "Grade or target level missing.", source_grade=f.source_grade,
                    aud_equivalent_level=f.target_level)
    if f.target_level not in levels:
        return _res(p, rule_id, NA, "Rule applies to a different level band.", aud_equivalent_level=f.target_level)
    meets = _grade_meets(p, f.source_grade, minimum)
    if meets is None:
        return _res(p, rule_id, REVIEW, f"Grade '{f.source_grade}' not in the documented grade scale.",
                    source_grade=f.source_grade)
    return _res(p, rule_id, PASS if meets else FAIL,
                f"Grade {f.source_grade} {'meets' if meets else 'does not meet'} minimum {minimum}.",
                aud_equivalent_level=f.target_level, source_grade=f.source_grade, minimum=minimum)


def check_grade_lower(p: Policy, f: CourseFacts) -> RuleResult:
    return _grade_rule(p, f, "AUD_GRADE_LOWER", range(100, 300), p.min_grade_lower)


def check_grade_upper(p: Policy, f: CourseFacts) -> RuleResult:
    return _grade_rule(p, f, "AUD_GRADE_UPPER", range(300, 500), p.min_grade_upper)


def check_restricted(p: Policy, f: CourseFacts) -> RuleResult:
    if not f.target_type_verified:
        return _res(p, "AUD_RESTRICTED_COURSE_TYPES", REVIEW, "Target course type not verified.")
    t = (f.target_restricted_type or "").lower()
    if t in p.restricted_types:
        return _res(p, "AUD_RESTRICTED_COURSE_TYPES", FAIL, f"Target course type '{t}' is not eligible for transfer credit.",
                    restricted_type=t)
    return _res(p, "AUD_RESTRICTED_COURSE_TYPES", PASS, "Target course is not a restricted type.")


def check_aggregate_cap(p: Policy, program_credits: Decimal | None, already_transferred: Decimal | None,
                        proposed: Decimal | None) -> RuleResult:
    if program_credits is None or already_transferred is None or proposed is None or program_credits <= 0:
        return _res(p, "AUD_MAX_50_PERCENT", REVIEW, "Program total or transfer totals missing.")
    cap = program_credits * p.max_transfer_fraction
    total = already_transferred + proposed
    ok = total <= cap
    return _res(p, "AUD_MAX_50_PERCENT", PASS if ok else FAIL,
                f"Transferred credits would total {total} against a cap of {cap}.",
                program_credits=str(program_credits), total_transferred=str(total), cap=str(cap))


STATIC_FLAGS = {
    "AUD_CREDIT_EQUIVALENCE": (REVIEW, "Credit unit conversion (semester/quarter) must be confirmed by staff."),
    "AUD_MIN_RESIDENCY": (REVIEW, "Residency requirements must be confirmed by staff."),
    "AUD_APPLICATION_WINDOW": (REVIEW, "Application timing must be confirmed against dates."),
    "AUD_MAJOR_SPECIFIC_CAP": (REVIEW, "Major-specific cap needs AUD confirmation."),
    "AUD_AFTER_ENROLLMENT_SPECIAL": (REVIEW, "After-enrollment / study-abroad conditions are out of the basic flow."),
    "AUD_INSTITUTION_RECOGNITION": ("NEEDS_HUMAN_VERIFICATION", "Institution recognition must be verified by authorized offices."),
}


def evaluate_course(p: Policy, f: CourseFacts) -> list[RuleResult]:
    results = [check_min_level(p, f), check_grade_lower(p, f), check_grade_upper(p, f), check_restricted(p, f)]
    results += [_res(p, rid, st, why) for rid, (st, why) in STATIC_FLAGS.items()]
    return results


def has_hard_fail(results: list[RuleResult]) -> bool:
    return any(r.status == FAIL for r in results)
