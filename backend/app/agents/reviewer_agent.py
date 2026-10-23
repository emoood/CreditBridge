import json
from dataclasses import dataclass, field
from pathlib import Path

from ..domain.schemas import ReviewOutput
from ..providers.base import LLMProvider
from ..services.policy_rules import RuleResult
from .loop import AgentOutcome, run_bounded_agent
from .matching_agent import MatchResult
from .tools.base import ToolContext, ToolRegistry

PROMPT_VERSION = "reviewer_v4"
PROMPT = (Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.txt").read_text(encoding="utf-8")
ENFORCED = {"AUD_NON_TRANSFERABLE_AREA", "AUD_MIN_LEVEL", "AUD_GRADE_LOWER", "AUD_GRADE_UPPER", "AUD_RESTRICTED_COURSE_TYPES", "AUD_MAX_50_PERCENT"}


@dataclass
class ReviewResult:
    status: str  # SUPPORTED_FOR_HUMAN_REVIEW | CHALLENGED | INSUFFICIENT_EVIDENCE | NOT_RUN | NEEDS_HUMAN_REVIEW
    output: ReviewOutput | None = None
    flags: list[str] = field(default_factory=list)
    agent: AgentOutcome | None = None


async def run_review(provider: LLMProvider, registry: ToolRegistry, ctx: ToolContext, match: MatchResult,
                     rules: list[RuleResult], max_rounds: int = 6) -> ReviewResult:
    if match.status != "POTENTIAL_MATCH" or match.output is None:
        return ReviewResult("NOT_RUN", flags=["NO_RECOMMENDATION_TO_REVIEW"])
    repo, m = ctx.repo, match.output
    src = repo.sources[ctx.source_course_id]
    ctx.match_cited = {"pairs": [{"source_excerpt_id": s.source_excerpt_id, "target_excerpt_id": s.target_excerpt_id}
                                 for s in m.supported_outcomes]}
    tgt = repo.targets[m.target_course_id]
    # Primary evidence only. The matcher's explanations are deliberately omitted to reduce agreement bias.
    user = json.dumps({
        "source_course": {"id": src.id, "title": src.title, "credits": src.credits,
                          "excerpts": [{"id": i, "text": repo.store.get(i).text} for i in src.excerpt_ids]},
        "target_course": {"id": tgt["code"], "title": tgt["title"], "level": tgt["level"], "credits": tgt["credits"],
                          "excerpts": [{"id": o["id"], "text": o["text"]} for o in tgt["outcomes"]]},
        "matcher_recommendation": {"recommendation": m.recommendation, "target_course_id": m.target_course_id,
                                   "cited_pairs": ctx.match_cited["pairs"]},
        "policy_engine_results_authoritative": [{"rule": r.rule_id, "status": r.status, "reason": r.reason}
                                                 for r in rules if r.rule_id in ENFORCED and r.status != "NOT_APPLICABLE"],
        "note": "Staff checklist items (residency, application window, etc.) are handled by humans; do not raise them as objections.",
    }, ensure_ascii=False)
    outcome = await run_bounded_agent(provider, registry, ctx, system=PROMPT, user=user, final_model=ReviewOutput,
                                      max_tool_rounds=max_rounds, stage="review")
    res = ReviewResult("NEEDS_HUMAN_REVIEW", flags=list(outcome.flags), agent=outcome)
    if outcome.status != "OK" or outcome.output is None:
        res.flags.append(outcome.error or "AGENT_FAILED")
        return res
    out: ReviewOutput = outcome.output
    for ob in out.objections:  # keep only objections' citations that resolve
        ok, bad = repo.store.validate_citations(ob.excerpt_ids)
        ob.excerpt_ids = ok
        res.flags += [f"INVALID_CITATION_REMOVED:{x}" for x in bad]
    res.output, res.status = out, out.verdict
    return res
