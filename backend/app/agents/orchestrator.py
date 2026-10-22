"""Runs one subject through the full case: investigate -> rules -> adversarial review -> (one revision round) -> triage.
Fixed, auditable workflow around the two agents. Nothing here can approve credit."""
from dataclasses import dataclass, field
from typing import Any

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
    contested: bool = False
    revised: bool = False
    llm_calls: int = 0
    provider: str = ""
    model: str = ""
    prompt_versions: dict[str, str] = field(default_factory=lambda: {"matching": MATCH_PROMPT, "review": REVIEW_PROMPT})
    flags: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        m = self.match.output
        return {"course": self.source_course_id, "match": self.match.status, "target": m.target_course_id if m else None,
                "strength": m.evidence_strength if m else None, "review": self.review.status if self.review else None,
                "revised": self.revised, "contested": self.contested, "triage": self.triage.category,
                "reasons": self.triage.reasons, "llm_calls": self.llm_calls, "flags": self.flags}


async def run_case(provider: LLMProvider, repo: DemoRepo, retriever: CatalogRetriever, source_code: str,
                   precedent: PrecedentStore | None = None, matcher_rounds: int = 10, reviewer_rounds: int = 6) -> CaseResult:
    registry = ToolRegistry(ALL_TOOLS)
    case, prec = CaseFile(), precedent or PrecedentStore()

    def ctx(role: str) -> ToolContext:  # both agents share one case file and one precedent memory
        return ToolContext(repo, retriever, role, source_code, case=case, precedent=prec)

    events.emit("stage", stage="case_start", course=source_code)
    match = await run_matching(provider, registry, ctx("matcher"), matcher_rounds)
    calls = match.agent.llm_calls if match.agent else 0
    rules: list[pr.RuleResult] = []
    review: ReviewResult | None = None
    revised = contested = False
    flags = list(match.flags)

    if match.status == "POTENTIAL_MATCH" and match.output:
        rules = enforced_rules(ctx("matcher"), match.output.target_course_id)
        events.emit("stage", stage="review", course=source_code)
        review = await run_review(provider, registry, ctx("reviewer"), match, rules, reviewer_rounds)
        calls += review.agent.llm_calls if review.agent else 0

        if review.status == "CHALLENGED" and review.output:  # one bounded revision round
            events.emit("stage", stage="revision", course=source_code)
            prior = match.output
            objections = [o.text for o in review.output.objections] or ["The reviewer challenged this match."]
            revised_match = await run_matching(provider, registry, ctx("matcher"), matcher_rounds, objections, prior)
            calls += revised_match.agent.llm_calls if revised_match.agent else 0
            revised = True
            flags += [f"REVISION:{f}" for f in revised_match.flags]
            if revised_match.status != "NEEDS_HUMAN_REVIEW":  # a failed revision keeps the original claim
                match = revised_match
            if match.status == "POTENTIAL_MATCH" and match.output:
                rules = enforced_rules(ctx("matcher"), match.output.target_course_id)
                review = await run_review(provider, registry, ctx("reviewer"), match, rules, reviewer_rounds)
                calls += review.agent.llm_calls if review.agent else 0
                contested = review.status == "CHALLENGED"
            else:
                review = None  # withdrawn or downgraded: nothing left to review
                rules = []

    declined = False
    if match.output and match.output.target_course_id:
        subj = repo.sources[source_code].title
        declined = any(p["decision"] == "DECLINED" and p["target_course_id"] == match.output.target_course_id
                       for p in prec.search(subj))
    t = triage(match.status, match.output.evidence_strength if match.output else None,
               review.status if review else "NOT_RUN", rules, contested, declined, match.flags)
    events.emit("stage", stage="case_done", course=source_code, triage=t.category)
    return CaseResult(source_code, match, review, rules, t, case, contested, revised, calls,
                      getattr(provider, "name", ""), getattr(provider, "model", ""), flags=flags)
