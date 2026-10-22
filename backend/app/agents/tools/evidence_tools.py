"""Read-only tools for both agents. Every tool records what it touched in the shared CaseFile.
The only writes are case notes and an in-app syllabus request draft."""
from typing import ClassVar

from pydantic import BaseModel, Field
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from ...services import policy_rules as pr
from ...services.catalog_retriever import MAX_TOP_K
from .base import AgentTool, ToolContext, ToolError


class CourseIdArgs(BaseModel):
    course_id: str


class SearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    top_k: int = Field(default=3, ge=1, le=10)  # clamped to 3 server-side


class ExcerptArgs(BaseModel):
    excerpt_id: str


class SyllabusRequestArgs(BaseModel):
    course_id: str
    missing_fields: list[str] = Field(default_factory=list, max_length=10)


class EmptyArgs(BaseModel):
    pass


class TargetArgs(BaseModel):
    target_course_id: str


class PrecedentArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300)


class NoteArgs(BaseModel):
    kind: str = Field(pattern="^(hypothesis|finding|open_question)$")
    text: str = Field(min_length=3, max_length=600)


def _check_scope(ctx: ToolContext, course_id: str) -> None:
    if course_id != ctx.source_course_id:
        raise ToolError(f"Course '{course_id}' is outside the current evaluation scope.")


def _target(ctx: ToolContext, code: str) -> dict:
    t = ctx.repo.targets.get(code)
    if not t:
        raise ToolError(f"Unknown target course '{code}'.")
    return t


def enforced_rules(ctx: ToolContext, target_code: str) -> list[pr.RuleResult]:
    """The deterministic rule results for this subject against a target. Same code the pipeline uses."""
    t, sc = _target(ctx, target_code), ctx.repo.sources[ctx.source_course_id]
    facts = pr.CourseFacts(sc.grade, t["level"], t["restricted_type"], True, t.get("department") or t["code"].split()[0])
    return [r for r in pr.evaluate_course(pr.Policy.load(), facts) if r.rule_id in ENFORCED]


ENFORCED = {"AUD_NON_TRANSFERABLE_AREA", "AUD_MIN_LEVEL", "AUD_GRADE_LOWER", "AUD_GRADE_UPPER", "AUD_RESTRICTED_COURSE_TYPES", "AUD_MAX_50_PERCENT"}


class FindSourceSyllabus(AgentTool):
    name = "find_source_syllabus"
    description = "Get the subject's outcome excerpts (ids and text). Returns syllabus=null if none is on file."
    input_schema = CourseIdArgs
    roles: ClassVar[set[str]] = {"matcher", "reviewer"}

    async def execute(self, args, ctx):
        _check_scope(ctx, args.course_id)
        sc = ctx.repo.sources[args.course_id]
        if not sc.syllabus_doc_id:
            return {"syllabus": None, "note": "No syllabus on file. Use request_syllabus and report NEEDS_MORE_EVIDENCE."}
        for i in sc.excerpt_ids:
            ctx.case.read_excerpt(ctx.role, i)
        return {"course": sc.title, "excerpts": [{"id": i, "text": ctx.repo.store.get(i).text} for i in sc.excerpt_ids]}


class GetTargetCourse(AgentTool):
    name = "get_target_course"
    description = "Get a target catalog course: title, level, credits, description and outcome excerpts (ids and text)."
    input_schema = CourseIdArgs
    roles: ClassVar[set[str]] = {"matcher", "reviewer"}

    async def execute(self, args, ctx):
        t = _target(ctx, args.course_id)
        ctx.case.read_target(ctx.role, t["code"])
        for o in t["outcomes"]:
            ctx.case.read_excerpt(ctx.role, o["id"])
        return {"code": t["code"], "title": t["title"], "level": t["level"], "credits": t["credits"],
                "description": t["description"], "excerpts": [{"id": o["id"], "text": o["text"]} for o in t["outcomes"]]}


class SearchTargetCatalog(AgentTool):
    name = "search_target_catalog"
    description = ("Search the target catalog by keywords. Returns at most 3 candidates; the score is retrieval relevance "
                   "only, not equivalence. Try different queries if results look wrong.")
    input_schema = SearchArgs
    roles: ClassVar[set[str]] = {"matcher"}

    async def execute(self, args, ctx):
        res = ctx.retriever.search(args.query, min(args.top_k, MAX_TOP_K))
        ctx.case.queries.append(args.query)
        for c in res:
            ctx.case.search_hits[c.course_id] = max(c.score, ctx.case.search_hits.get(c.course_id, 0))
        return [{"course_id": c.course_id, "title": ctx.repo.targets[c.course_id]["title"], "relevance": c.score} for c in res]


class GetSourceExcerpt(AgentTool):
    name = "get_source_excerpt"
    description = "Fetch one stored excerpt by id (subject outcome or target outcome)."
    input_schema = ExcerptArgs
    roles: ClassVar[set[str]] = {"matcher", "reviewer"}

    async def execute(self, args, ctx):
        ex = ctx.repo.store.get(args.excerpt_id)
        sc = ctx.repo.sources[ctx.source_course_id]
        if ex is None:
            raise ToolError(f"Excerpt '{args.excerpt_id}' does not exist.")
        if ex.document_id.startswith("src-doc-") and ex.document_id != sc.syllabus_doc_id:
            raise ToolError("Excerpt is outside the current evaluation scope.")
        ctx.case.read_excerpt(ctx.role, ex.id)
        return {"id": ex.id, "document_id": ex.document_id, "page": ex.page, "text": ex.text}


class GetPolicySummary(AgentTool):
    name = "get_policy_summary"
    description = "Read-only summary of the transfer policy. The Python rules engine is authoritative."
    input_schema = EmptyArgs
    roles: ClassVar[set[str]] = {"matcher", "reviewer"}

    async def execute(self, args, ctx):
        p = pr.Policy.load()
        return {"policy_version_id": p.policy_version_id, "min_level": p.min_level,
                "min_grade_100_200": p.min_grade_lower, "min_grade_300_400": p.min_grade_upper,
                "restricted_types": p.restricted_types, "note": "Rule results are computed by code and cannot be changed."}


class CheckRules(AgentTool):
    name = "check_rules"
    description = ("Read-only what-if: run the deterministic policy rules for this subject (its real grade) against a target "
                   "course. Use it to learn whether a candidate would be blocked. You cannot change the result.")
    input_schema = TargetArgs
    roles: ClassVar[set[str]] = {"matcher", "reviewer"}

    async def execute(self, args, ctx):
        res = [{"rule": r.rule_id, "status": r.status, "reason": r.reason}
               for r in enforced_rules(ctx, args.target_course_id) if r.status != "NOT_APPLICABLE"]
        ctx.case.rule_checks.append({"by": ctx.role, "target": args.target_course_id, "results": res})
        return res


class CompareOutcomes(AgentTool):
    name = "compare_outcomes"
    description = ("Lexical hint only: for each outcome of a target course, the most similar subject outcome and a 0-1 "
                   "similarity. Low similarity suggests a gap. It is not a judgment; you must still read the text.")
    input_schema = TargetArgs
    roles: ClassVar[set[str]] = {"matcher", "reviewer"}

    async def execute(self, args, ctx):
        t, sc = _target(ctx, args.target_course_id), ctx.repo.sources[ctx.source_course_id]
        if not sc.excerpt_ids:
            raise ToolError("No subject outcomes on file to compare.")
        src = [(i, ctx.repo.store.get(i).text) for i in sc.excerpt_ids]
        tgt = [(o["id"], o["text"]) for o in t["outcomes"]]
        vec = TfidfVectorizer(stop_words="english").fit([x[1] for x in src + tgt])
        sims = cosine_similarity(vec.transform([x[1] for x in tgt]), vec.transform([x[1] for x in src]))
        rows = []
        for ti, (tid, _) in enumerate(tgt):
            j = int(sims[ti].argmax())
            rows.append({"target_excerpt": tid, "best_subject_excerpt": src[j][0], "similarity": round(float(sims[ti][j]), 2)})
        return {"pairs": rows, "possible_gaps": [r["target_excerpt"] for r in rows if r["similarity"] < 0.15]}


class LookupPrecedent(AgentTool):
    name = "lookup_precedent"
    description = "Search past admissions-officer decisions for similar subjects. Returns up to 3, possibly none."
    input_schema = PrecedentArgs
    roles: ClassVar[set[str]] = {"matcher", "reviewer"}

    async def execute(self, args, ctx):
        res = ctx.precedent.search(args.query)
        ctx.case.precedents_seen.extend(res)
        return res or {"precedents": [], "note": "No similar past decisions on file."}


class AddCaseNote(AgentTool):
    name = "add_case_note"
    description = "Record a hypothesis, finding or open question in the case file the officer will see. Keep it short."
    input_schema = NoteArgs
    roles: ClassVar[set[str]] = {"matcher", "reviewer"}

    async def execute(self, args, ctx):
        ctx.case.note(ctx.role, args.kind, args.text)
        return {"recorded": True}


class RequestSyllabus(AgentTool):
    name = "request_syllabus"
    description = "Draft an in-app request for missing evidence. Nothing is sent externally."
    input_schema = SyllabusRequestArgs
    roles: ClassVar[set[str]] = {"matcher"}

    async def execute(self, args, ctx):
        _check_scope(ctx, args.course_id)
        req = {"course_id": args.course_id, "missing_fields": args.missing_fields, "status": "DRAFT_IN_APP"}
        ctx.repo.syllabus_requests.append(req)
        ctx.case.requests.append(req)
        return req


class GetMatchEvidence(AgentTool):
    name = "get_match_evidence"
    description = "Get the subject and target text for each outcome pair the matcher cited."
    input_schema = EmptyArgs
    roles: ClassVar[set[str]] = {"reviewer"}

    async def execute(self, args, ctx):
        pairs = []
        for p in (ctx.match_cited or {}).get("pairs", []):
            s, t = ctx.repo.store.get(p["source_excerpt_id"]), ctx.repo.store.get(p["target_excerpt_id"])
            pairs.append({"source": s.text if s else None, "target": t.text if t else None, **p})
        return pairs


ALL_TOOLS = [FindSourceSyllabus(), GetTargetCourse(), SearchTargetCatalog(), GetSourceExcerpt(), GetPolicySummary(),
             CheckRules(), CompareOutcomes(), LookupPrecedent(), AddCaseNote(), RequestSyllabus(), GetMatchEvidence()]
