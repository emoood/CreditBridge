from typing import ClassVar

from pydantic import BaseModel, Field

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


def _check_scope(ctx: ToolContext, course_id: str) -> None:
    if course_id != ctx.source_course_id:
        raise ToolError(f"Course '{course_id}' is outside the current evaluation scope.")


class FindSourceSyllabus(AgentTool):
    name = "find_source_syllabus"
    description = "Get the source course's syllabus excerpts (ids and text) for the course being evaluated."
    input_schema = CourseIdArgs
    roles: ClassVar[set[str]] = {"matcher", "reviewer"}

    async def execute(self, args, ctx):
        _check_scope(ctx, args.course_id)
        sc = ctx.repo.sources[args.course_id]
        if not sc.syllabus_doc_id:
            return {"syllabus": None, "note": "No syllabus on file."}
        return {"course": sc.title, "excerpts": [{"id": i, "text": ctx.repo.store.get(i).text} for i in sc.excerpt_ids]}


class GetTargetCourse(AgentTool):
    name = "get_target_course"
    description = "Get a target catalog course: title, level, credits, description and outcome excerpts (ids and text)."
    input_schema = CourseIdArgs
    roles: ClassVar[set[str]] = {"matcher", "reviewer"}

    async def execute(self, args, ctx):
        t = ctx.repo.targets.get(args.course_id)
        if not t:
            raise ToolError(f"Unknown target course '{args.course_id}'.")
        return {"code": t["code"], "title": t["title"], "level": t["level"], "credits": t["credits"],
                "description": t["description"],
                "excerpts": [{"id": o["id"], "text": o["text"]} for o in t["outcomes"]]}


class SearchTargetCatalog(AgentTool):
    name = "search_target_catalog"
    description = "Search the target catalog. Returns at most 3 candidates; the score is retrieval relevance only, not equivalence."
    input_schema = SearchArgs
    roles: ClassVar[set[str]] = {"matcher"}

    async def execute(self, args, ctx):
        res = ctx.retriever.search(args.query, min(args.top_k, MAX_TOP_K))
        return [{"course_id": c.course_id, "title": ctx.repo.targets[c.course_id]["title"], "relevance": c.score} for c in res]


class GetSourceExcerpt(AgentTool):
    name = "get_source_excerpt"
    description = "Fetch one stored excerpt by id (source syllabus or target outcome)."
    input_schema = ExcerptArgs
    roles: ClassVar[set[str]] = {"matcher", "reviewer"}

    async def execute(self, args, ctx):
        ex = ctx.repo.store.get(args.excerpt_id)
        sc = ctx.repo.sources[ctx.source_course_id]
        if ex is None:
            raise ToolError(f"Excerpt '{args.excerpt_id}' does not exist.")
        if ex.document_id.startswith("src-doc-") and ex.document_id != sc.syllabus_doc_id:
            raise ToolError("Excerpt is outside the current evaluation scope.")
        return {"id": ex.id, "document_id": ex.document_id, "page": ex.page, "text": ex.text}


class GetPolicySummary(AgentTool):
    name = "get_policy_summary"
    description = "Read-only summary of the transfer policy. The Python rules engine is authoritative."
    input_schema = EmptyArgs
    roles: ClassVar[set[str]] = {"matcher", "reviewer"}

    async def execute(self, args, ctx):
        from ...services.policy_rules import Policy
        p = Policy.load()
        return {"policy_version_id": p.policy_version_id, "min_level": p.min_level,
                "min_grade_100_200": p.min_grade_lower, "min_grade_300_400": p.min_grade_upper,
                "restricted_types": p.restricted_types, "note": "Rule results are computed by code and cannot be changed."}


class RequestSyllabus(AgentTool):
    name = "request_syllabus"
    description = "Draft an in-app request for a missing syllabus. Nothing is sent externally."
    input_schema = SyllabusRequestArgs
    roles: ClassVar[set[str]] = {"matcher"}

    async def execute(self, args, ctx):
        _check_scope(ctx, args.course_id)
        req = {"course_id": args.course_id, "missing_fields": args.missing_fields, "status": "DRAFT_IN_APP"}
        ctx.repo.syllabus_requests.append(req)
        return req


class GetMatchEvidence(AgentTool):
    name = "get_match_evidence"
    description = "Get the source and target text for each outcome pair the matcher cited."
    input_schema = EmptyArgs
    roles: ClassVar[set[str]] = {"reviewer"}

    async def execute(self, args, ctx):
        pairs = []
        for p in (ctx.match_cited or {}).get("pairs", []):
            s, t = ctx.repo.store.get(p["source_excerpt_id"]), ctx.repo.store.get(p["target_excerpt_id"])
            pairs.append({"source": s.text if s else None, "target": t.text if t else None, **p})
        return pairs


ALL_TOOLS = [FindSourceSyllabus(), GetTargetCourse(), SearchTargetCatalog(), GetSourceExcerpt(),
             GetPolicySummary(), RequestSyllabus(), GetMatchEvidence()]
