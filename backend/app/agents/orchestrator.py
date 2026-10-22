"""Runs one subject through the full case: investigate -> rules -> adversarial review -> triage.
The reviewer is advisory: its objections are shown next to the pick and never fed back to the matcher.
Fixed, auditable workflow around the two agents. Nothing here can approve credit."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..devlog import events
from ..providers.base import LLMProvider
from ..services import policy_rules as pr
from ..services.catalog_retriever import CatalogRetriever
from ..services.demo_repo import DemoRepo
from ..services.triage import Triage, triage
from .case_file import CaseFile
from .matching_agent import PROMPT_VERSION as MATCH_PROMPT
from .matching_agent import MatchResult, run_matching
from .precedent import PrecedentStore
from .reviewer_agent import PROMPT_VERSION as REVIEW_PROMPT
from .reviewer_agent import ReviewResult, run_review
from .tools.base import ToolContext, ToolRegistry
from .tools.evidence_tools import ALL_TOOLS, enforced_rules


@dataclass
class CaseResult:
    source_course_id: str
    match: MatchResult
    review: ReviewResult | None
    rules: list[pr.RuleResult]
    triage: Triage
    case: CaseFile
    llm_calls: int = 0
    provider: str = ""
    model: str = ""
    prompt_versions: dict[str, str] = field(default_factory=lambda: {"matching": MATCH_PROMPT, "review": REVIEW_PROMPT})
    flags: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        m = self.match.output
        return {"course": self.source_course_id, "match": self.match.status, "target": m.target_course_id if m else None,
                "strength": m.evidence_strength if m else None, "review": self.review.status if self.review else None,
                "triage": self.triage.category,
                "reasons": self.triage.reasons, "llm_calls": self.llm_calls, "flags": self.flags}


async def run_case(provider: LLMProvider, repo: DemoRepo, retriever: CatalogRetriever, source_code: str,
                   precedent: PrecedentStore | None = None, matcher_rounds: int = 6, reviewer_rounds: int = 6) -> CaseResult:
    case, prec = CaseFile(), precedent or PrecedentStore()
    # lookup_precedent costs the model a turn and returns nothing until real officer decisions exist: leave it out until then
    registry = ToolRegistry([t for t in ALL_TOOLS if t.name != "lookup_precedent" or prec.items])

    def ctx(role: str) -> ToolContext:  # both agents share one case file and one precedent memory
        return ToolContext(repo, retriever, role, source_code, case=case, precedent=prec)

    events.emit("stage", stage="case_start", course=source_code)
    match = await run_matching(provider, registry, ctx("matcher"), matcher_rounds)
    calls = match.agent.llm_calls if match.agent else 0
    rules: list[pr.RuleResult] = []
    review: ReviewResult | None = None
    flags = list(match.flags)

    if match.status == "POTENTIAL_MATCH" and match.output:
        rules = enforced_rules(ctx("matcher"), match.output.target_course_id)
        events.emit("stage", stage="review", course=source_code)
        review = await run_review(provider, registry, ctx("reviewer"), match, rules, reviewer_rounds)
        calls += review.agent.llm_calls if review.agent else 0

    declined = False
    if match.output and match.output.target_course_id:
        subj = repo.sources[source_code].title
        declined = any(p["decision"] == "DECLINED" and p["target_course_id"] == match.output.target_course_id
                       for p in prec.search(subj))
    t = triage(match.status, match.output.evidence_strength if match.output else None,
               review.status if review else "NOT_RUN", rules, declined, match.flags)
    events.emit("stage", stage="case_done", course=source_code, triage=t.category)
    return CaseResult(source_code, match, review, rules, t, case, calls,
                      getattr(provider, "name", ""), getattr(provider, "model", ""), flags=flags)


SCHOOL_SPECIFIC_FILE = Path(__file__).resolve().parents[3] / "policies" / "school_specific_subjects.yaml"


def is_school_specific(title: str) -> bool:
    """Subjects that exist only in the school system (religion, Arabic, PE, ...): never mapped, never sent to the model."""
    t = (title or "").lower()
    return any(p.lower() in t for p in yaml.safe_load(SCHOOL_SPECIFIC_FILE.read_text())["patterns"])


def school_specific_case(repo: DemoRepo, source_code: str) -> CaseResult:
    from .matching_agent import MatchResult
    case = CaseFile()
    match = MatchResult("SCHOOL_SPECIFIC", flags=["SCHOOL_SPECIFIC_NOT_MAPPED"], case=case)
    t = Triage("SCHOOL_SPECIFIC", ["School-specific subject: it has no university equivalent and is neither mapped nor suggested as a general elective."], 6)
    return CaseResult(source_code, match, None, [], t, case, flags=match.flags)


def no_evidence_case(repo: DemoRepo, source_code: str) -> CaseResult:
    """A subject with no curriculum evidence (unrecognized): NEEDS_SYLLABUS, a drafted in-app request, no model call."""
    from .matching_agent import MatchResult
    case = CaseFile()
    if not any(q["course_id"] == source_code for q in repo.syllabus_requests):
        req = {"course_id": source_code, "missing_fields": ["syllabus"], "status": "DRAFT_IN_APP"}
        repo.syllabus_requests.append(req)
        case.requests.append(req)
    match = MatchResult("NEEDS_SYLLABUS", flags=["NO_SOURCE_SYLLABUS", "SUBJECT_NOT_IN_CURRICULUM"], case=case)
    t = triage("NEEDS_SYLLABUS", None, "NOT_RUN", [], False, match.flags)
    return CaseResult(source_code, match, None, [], t, case, flags=match.flags)
