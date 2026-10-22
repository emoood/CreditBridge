"""Turns agent signals + rule results + precedent into the officer's worklist category. Pure code, no LLM."""
from dataclasses import dataclass

from .policy_rules import FAIL, REVIEW, RuleResult

ENFORCED = {"AUD_MIN_LEVEL", "AUD_GRADE_LOWER", "AUD_GRADE_UPPER", "AUD_RESTRICTED_COURSE_TYPES", "AUD_MAX_50_PERCENT"}


@dataclass
class Triage:
    category: str  # READY_TO_ACCEPT | NEEDS_ATTENTION | BLOCKED_BY_RULE | NEEDS_EVIDENCE | POTENTIAL_GE | NO_MATCH
    reasons: list[str]
    priority: int  # lower = look at first


def triage(match_status: str, evidence_strength: str | None, review_status: str, rules: list[RuleResult],
           declined_precedent: bool, flags: list[str]) -> Triage:
    if match_status in ("NEEDS_SYLLABUS", "NEEDS_MORE_EVIDENCE"):
        return Triage("NEEDS_EVIDENCE", ["Evidence is missing or insufficient."] + flags[:3], 2)
    if match_status == "NO_DEFENSIBLE_MATCH":
        return Triage("NO_MATCH", ["No defensible AUD course found."], 5)
    if match_status == "POTENTIAL_GENERAL_ELECTIVE":
        return Triage("POTENTIAL_GE", ["No specific course fits; possible general elective. The AI only flags it; the officer decides."], 1)  # needs a human judgment, so it sits with needs-attention
    if match_status != "POTENTIAL_MATCH":
        return Triage("NEEDS_ATTENTION", ["The agent did not complete; review manually."], 1)
    fails = [r for r in rules if r.rule_id in ENFORCED and r.status == FAIL]
    if fails:
        return Triage("BLOCKED_BY_RULE", [f"{r.rule_id}: {r.reason}" for r in fails], 2)
    reasons = []
    if review_status == "CHALLENGED":
        reasons.append("The reviewer objects to this match (objections shown). The matcher's pick is unchanged.")
    elif review_status != "SUPPORTED_FOR_HUMAN_REVIEW":
        reasons.append(f"Reviewer verdict: {review_status}.")
    if evidence_strength != "STRONG":
        reasons.append(f"Evidence strength: {evidence_strength}.")
    if any(r.rule_id in ENFORCED and r.status == REVIEW for r in rules):
        reasons.append("A policy rule needs human review (missing data).")
    if declined_precedent:
        reasons.append("A similar past decision declined this course.")
    if reasons:
        return Triage("NEEDS_ATTENTION", reasons, 1)
    return Triage("READY_TO_ACCEPT", ["Strong evidence, reviewer supports, rules pass."], 4)
