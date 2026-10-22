from dataclasses import dataclass, field
from pathlib import Path

from ..domain.schemas import MatchOutput
from ..providers.base import LLMProvider
from ..services.catalog_retriever import Candidate
from .loop import AgentOutcome, run_bounded_agent
from .tools.base import ToolContext, ToolRegistry

PROMPT = (Path(__file__).resolve().parent / "prompts" / "matching_v1.txt").read_text()
PROMPT_VERSION = "matching_v1"


@dataclass
class MatchResult:
    status: str  # NEEDS_SYLLABUS | NO_DEFENSIBLE_MATCH | NEEDS_MORE_EVIDENCE | POTENTIAL_MATCH | NEEDS_HUMAN_REVIEW
    output: MatchOutput | None = None
    candidates: list[Candidate] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    agent: AgentOutcome | None = None


async def run_matching(provider: LLMProvider, registry: ToolRegistry, ctx: ToolContext, max_rounds: int = 3) -> MatchResult:
    repo, src = ctx.repo, ctx.repo.sources[ctx.source_course_id]
    # Deterministic pre-checks: no LLM guess when evidence is missing.
    if not src.syllabus_doc_id or not src.excerpt_ids:
        repo.syllabus_requests.append({"course_id": src.id, "missing_fields": ["syllabus"], "status": "DRAFT_IN_APP"})
        return MatchResult("NEEDS_SYLLABUS", flags=["NO_SOURCE_SYLLABUS"])
    query = src.title + " " + " ".join(repo.store.get(i).text for i in src.excerpt_ids)
    cands = ctx.retriever.search(query)
    if not cands:
        return MatchResult("NO_DEFENSIBLE_MATCH", flags=["NO_RETRIEVAL_CANDIDATES"])

    cand_txt = "\n".join(f"- {c.course_id}: {repo.targets[c.course_id]['title']} (retrieval relevance {c.score})" for c in cands)
    user = (f"Source course to evaluate: {src.id} - {src.title} ({src.credits} credits).\n"
            f"Candidate target courses (retrieval relevance only, not equivalence):\n{cand_txt}\n"
            f"Fetch the source syllabus with find_source_syllabus and each plausible target with get_target_course, "
            f"then call submit_result. Use target_course_id from the candidates only.")
    outcome = await run_bounded_agent(provider, registry, ctx, system=PROMPT, user=user, final_model=MatchOutput,
                                      max_tool_rounds=max_rounds, stage="matching")
    res = MatchResult("NEEDS_HUMAN_REVIEW", candidates=cands, flags=list(outcome.flags), agent=outcome)
    if outcome.status != "OK" or outcome.output is None:
        res.flags.append(outcome.error or "AGENT_FAILED")
        return res
    out: MatchOutput = outcome.output
    res.output, res.status = out, out.recommendation

    if out.recommendation == "POTENTIAL_MATCH":
        tgt = out.target_course_id
        if tgt not in repo.targets or tgt not in {c.course_id for c in cands}:
            res.flags.append("TARGET_NOT_IN_CANDIDATES")
            res.status = "NEEDS_MORE_EVIDENCE"
            return res
        tgt_doc = repo.targets[tgt]["document_id"]
        kept = []
        for so in out.supported_outcomes:
            _, bad_s = repo.store.validate_citations([so.source_excerpt_id], {src.syllabus_doc_id})
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
