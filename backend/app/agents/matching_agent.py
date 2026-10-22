from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings
from ..domain.schemas import MatchOutput
from ..providers.base import LLMProvider
from .case_file import CaseFile
from .loop import AgentOutcome, run_bounded_agent
from .tools.base import ToolContext, ToolRegistry

PROMPT_VERSION = "matching_v4"
PROMPT = (Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.txt").read_text()


@dataclass
class MatchResult:
    status: str  # POTENTIAL_MATCH | POTENTIAL_GENERAL_ELECTIVE | SCHOOL_SPECIFIC (set by code, never by the model) | NO_DEFENSIBLE_MATCH | NEEDS_MORE_EVIDENCE |
    # NEEDS_SYLLABUS | NEEDS_HUMAN_REVIEW (agent failed)
    output: MatchOutput | None = None
    flags: list[str] = field(default_factory=list)
    agent: AgentOutcome | None = None
    case: CaseFile | None = None


async def run_matching(provider: LLMProvider, registry: ToolRegistry, ctx: ToolContext, max_rounds: int = 6) -> MatchResult:
    repo = ctx.repo
    sc = repo.sources.get(ctx.source_course_id)
    if sc is None:  # the one hard stop: nothing to reason about
        return MatchResult("NEEDS_MORE_EVIDENCE", flags=["SUBJECT_NOT_FOUND"], case=ctx.case)
    ctx.case.subject = {"id": sc.id, "title": sc.title, "credits": sc.credits}

    hint = ""
    if sc.excerpt_ids:  # code pre-retrieves likely candidates so the agent does not burn its tool budget searching
        query = sc.title + " " + " ".join(repo.store.get(i).text for i in sc.excerpt_ids)
        cands = ctx.retriever.search(query, subject_title=sc.title)
        if cands:
            hint = ("\nCatalog search suggests these candidates (retrieval relevance only, NOT equivalence; you may search further):\n"
                    + "\n".join(f"- {c.course_id}: {repo.targets[c.course_id]['title']} (relevance {c.score})" for c in cands))
            for c in cands:
                ctx.case.search_hits[c.course_id] = c.score
    user = f"Subject to evaluate: {sc.id} - {sc.title}. Investigate and submit your result.{hint}"
    title_only = not sc.syllabus_doc_id and get_settings().title_only_suggestions
    if title_only:
        cands = ctx.retriever.search(sc.title, subject_title=sc.title)
        hint = ("\nCatalog search by title suggests these courses (title relevance only):\n"
                + "\n".join(f"- {c.course_id}: {repo.targets[c.course_id]['title']} (level {repo.targets[c.course_id]['level']})" for c in cands)) if cands else ""
        user = (f"Subject to evaluate: {sc.id} - {sc.title}. Level or term on the transcript: {sc.term or 'not stated'}.\n"
                "SPECIAL CASE: NO syllabus or learning outcomes are on file for this subject, so you cannot compare outcomes and must NOT "
                "claim a match. Submit recommendation NEEDS_MORE_EVIDENCE with no target_course_id and no supported_outcomes. Instead fill "
                "title_only_suggestions with up to 3 AUD courses that look plausible from the subject's TITLE and LEVEL alone, each with a "
                "one-line reason. Use course codes that exist in the catalog (read them with get_target_course or take them from the "
                "list below). These are unverified guesses for the officer, not evidence. Be quick: at most two tool steps."
                f"{hint}")
        max_rounds = min(max_rounds, 2)

    outcome = await run_bounded_agent(provider, registry, ctx, system=PROMPT, user=user, final_model=MatchOutput,
                                      max_tool_rounds=max_rounds, stage="matching")
    res = MatchResult("NEEDS_HUMAN_REVIEW", flags=list(outcome.flags), agent=outcome, case=ctx.case)
    if not sc.syllabus_doc_id:  # referee: no evidence on file means no suggestion, whatever the agent did
        if not any(r["course_id"] == sc.id for r in repo.syllabus_requests):
            req = {"course_id": sc.id, "missing_fields": ["syllabus"], "status": "DRAFT_IN_APP"}
            repo.syllabus_requests.append(req)
            ctx.case.requests.append(req)
        res.flags.append("NO_SOURCE_SYLLABUS")
        res.status = "NEEDS_SYLLABUS"
        if title_only and outcome.status == "OK" and outcome.output is not None:
            seen, sugg = set(), []
            for t in outcome.output.title_only_suggestions:  # only real catalog courses, at most 3, no duplicates
                if t.target_course_id in repo.targets and t.target_course_id not in seen and len(sugg) < 3:
                    seen.add(t.target_course_id)
                    sugg.append(t)
            res.output = MatchOutput(recommendation="NEEDS_MORE_EVIDENCE", title_only_suggestions=sugg)  # no target, no citations
            res.flags.append("TITLE_ONLY_UNVERIFIED")
        return res
    if outcome.status != "OK" or outcome.output is None:
        res.flags.append(outcome.error or "AGENT_FAILED")
        return res
    out: MatchOutput = outcome.output
    res.output, res.status = out, out.recommendation

    # Referee checks (code). The agent investigates; these keep the record honest.
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
