from dataclasses import dataclass, field
from pathlib import Path

from ..domain.schemas import MatchOutput
from ..providers.base import LLMProvider
from .case_file import CaseFile
from .loop import AgentOutcome, run_bounded_agent
from .tools.base import ToolContext, ToolRegistry

PROMPT_VERSION = "matching_v2"
PROMPT = (Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.txt").read_text(encoding="utf-8")


@dataclass
class MatchResult:
    status: str  # POTENTIAL_MATCH | POTENTIAL_GENERAL_ELECTIVE | NO_DEFENSIBLE_MATCH | NEEDS_MORE_EVIDENCE |
    # NEEDS_SYLLABUS | NEEDS_HUMAN_REVIEW (agent failed)
    output: MatchOutput | None = None
    flags: list[str] = field(default_factory=list)
    agent: AgentOutcome | None = None
    case: CaseFile | None = None


async def run_matching(provider: LLMProvider, registry: ToolRegistry, ctx: ToolContext, max_rounds: int = 10,
                       objections: list[str] | None = None, prior: MatchOutput | None = None) -> MatchResult:
    repo = ctx.repo
    sc = repo.sources.get(ctx.source_course_id)
    if sc is None:  # the one hard stop: nothing to reason about
        return MatchResult("NEEDS_MORE_EVIDENCE", flags=["SUBJECT_NOT_FOUND"], case=ctx.case)
    ctx.case.subject = {"id": sc.id, "title": sc.title, "credits": sc.credits}

    user = f"Subject to evaluate: {sc.id} - {sc.title} ({sc.credits} credits). Investigate and submit your result."
    if objections:
        user += ("\n\nREVISION ROUND. You previously claimed: "
                 f"{prior.recommendation if prior else '?'} -> {prior.target_course_id if prior else None}. "
                 "The reviewer raised these objections:\n" + "\n".join(f"- {o}" for o in objections) +
                 "\nCheck them against the evidence. Revise, withdraw (change your recommendation), or defend with "
                 "evidence. Explain in response_to_objections.")

    outcome = await run_bounded_agent(provider, registry, ctx, system=PROMPT, user=user, final_model=MatchOutput,
                                      max_tool_rounds=max_rounds, stage="matching_revision" if objections else "matching")
    res = MatchResult("NEEDS_HUMAN_REVIEW", flags=list(outcome.flags), agent=outcome, case=ctx.case)
    if outcome.status != "OK" or outcome.output is None:
        res.flags.append(outcome.error or "AGENT_FAILED")
        return res
    out: MatchOutput = outcome.output
    res.output, res.status = out, out.recommendation

    # Referee checks (code). The agent investigates; these keep the record honest.
    if not sc.syllabus_doc_id:
        if not any(r["course_id"] == sc.id for r in repo.syllabus_requests):
            req = {"course_id": sc.id, "missing_fields": ["syllabus"], "status": "DRAFT_IN_APP"}
            repo.syllabus_requests.append(req)
            ctx.case.requests.append(req)
        res.flags.append("NO_SOURCE_SYLLABUS")
        res.status = "NEEDS_SYLLABUS"
        return res

    if out.recommendation == "POTENTIAL_GENERAL_ELECTIVE":
        out.target_course_id, out.supported_outcomes = None, []  # a flag only
        res.flags.append("GENERAL_ELECTIVE_FLAG_FOR_HUMAN")
        return res

    if out.recommendation == "POTENTIAL_MATCH":
        tgt = out.target_course_id
        if tgt not in repo.targets:
            res.flags.append("UNKNOWN_TARGET")
            res.status = "NEEDS_MORE_EVIDENCE"
            return res
        if tgt not in ctx.case.targets_read.get("matcher", set()):
            res.flags.append("TARGET_NEVER_READ")  # cannot claim a course the agent did not read
            res.status = "NEEDS_MORE_EVIDENCE"
            return res
        tgt_doc = repo.targets[tgt]["document_id"]
        kept = []
        for so in out.supported_outcomes:
            _, bad_s = repo.store.validate_citations([so.source_excerpt_id], {sc.syllabus_doc_id})
            _, bad_t = repo.store.validate_citations([so.target_excerpt_id], {tgt_doc})
            if bad_s or bad_t:
                res.flags += [f"INVALID_CITATION_REMOVED:{x}" for x in bad_s + bad_t]
            else:
                kept.append(so)
        out.supported_outcomes = kept
        if not kept:
            res.flags.append("NO_VALID_CITATIONS")
            res.status = "NEEDS_MORE_EVIDENCE"
    return res
