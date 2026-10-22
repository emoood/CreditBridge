"""Tools for the Case agent. Reads are free; the only writes are a drafted request and the row's state, both guarded by code."""
import uuid
from dataclasses import dataclass
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from ...config import get_settings
from ...db.models import RowState
from ...db.session import app_session
from ...providers.base import LLMProvider, ToolCall
from ...services import mapping as mp
from ...services.usage import trace
from ..orchestrator import CaseResult, run_case
from .base import AgentTool, ToolContext, ToolError, ToolRegistry, ToolResult
from .evidence_tools import EmptyArgs, TargetArgs, enforced_rules


@dataclass
class CaseContext(ToolContext):
    app_id: str = ""
    row_id: str = ""
    provider: LLMProvider | None = None
    proposal: CaseResult | None = None  # the matcher/reviewer result when propose_candidates ran


def _state(ctx: CaseContext, db) -> RowState:
    st = mp.get_state(db, ctx.app_id, ctx.row_id)
    if st is None:
        raise ToolError("This row has no state yet.")
    return st


def _state_facts(ctx: CaseContext) -> dict:
    with app_session() as db:
        st = mp.get_state(db, ctx.app_id, ctx.row_id)
        return dict(st.facts or {}) if st else {}


class TracedRegistry(ToolRegistry):
    """Every action the Case agent takes is logged to the trace shown in the activity drawer."""

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        res = await super().execute(call, ctx)
        if isinstance(ctx, CaseContext):
            trace(ctx.app_id, ctx.row_id, "case_agent", call.name, call.arguments, res.ok, (res.error or res.to_message())[:300])
        return res


class ReadMapping(AgentTool):
    name = "read_mapping"
    description = "Read this row's mapping, its condition, the code's condition verdict, missing evidence, facts on file and requests already drafted."
    input_schema = EmptyArgs
    roles: ClassVar[set[str]] = {"case"}

    async def execute(self, args, ctx: CaseContext):
        with app_session() as db:
            st = _state(ctx, db)
            return {"row_status": st.status, "kind": st.kind, "reason": st.reason, "mapping": st.mapping, "condition_result": st.condition_result,
                    "facts_on_file": {k: v for k, v in (st.facts or {}).items() if not k.startswith("_")}, "officer_notes": (st.facts or {}).get("_notes", []), "requests": st.requests, "grade_on_transcript": ctx.repo.sources[ctx.source_course_id].grade}


class ReadSourceExcerpts(AgentTool):
    name = "read_source_excerpts"
    description = "Read the school subject's outcome excerpts from the curriculum database (ids and text). Empty means no syllabus on file."
    input_schema = EmptyArgs
    roles: ClassVar[set[str]] = {"case"}

    async def execute(self, args, ctx: CaseContext):
        sc = ctx.repo.sources[ctx.source_course_id]
        return {"course": sc.title, "excerpts": [{"id": i, "text": ctx.repo.store.get(i).text} for i in sc.excerpt_ids[:10]]}


class TargetCourseArgs(BaseModel):
    course_id: str


class ReadTargetExcerpts(AgentTool):
    name = "read_target_excerpts"
    description = "Read an AUD course: title, level and its description or outcome excerpts."
    input_schema = TargetCourseArgs
    roles: ClassVar[set[str]] = {"case"}

    async def execute(self, args, ctx: CaseContext):
        t = ctx.repo.targets.get(args.course_id)
        if not t:
            raise ToolError(f"Unknown AUD course '{args.course_id}'.")
        return {"code": t["code"], "title": t["title"], "level": t["level"], "excerpts": [{"id": o["id"], "text": o["text"]} for o in t["outcomes"][:8]]}


class CheckPolicy(AgentTool):
    name = "check_policy"
    description = "Run the deterministic policy rules for this row's grade against an AUD course. Read-only; you cannot change the result."
    input_schema = TargetArgs
    roles: ClassVar[set[str]] = {"case"}

    async def execute(self, args, ctx: CaseContext):
        if args.target_course_id not in ctx.repo.targets:
            raise ToolError(f"Unknown AUD course '{args.target_course_id}'.")
        return [{"rule": r.rule_id, "status": r.status, "reason": r.reason} for r in enforced_rules(ctx, args.target_course_id) if r.status != "NOT_APPLICABLE"]


class ProposeCandidates(AgentTool):
    name = "propose_candidates"
    description = "For an UNMAPPED course only: run the matcher and the advisory reviewer and return candidate AUD courses, strength and objections."
    input_schema = EmptyArgs
    roles: ClassVar[set[str]] = {"case"}

    async def execute(self, args, ctx: CaseContext):
        with app_session() as db:
            kind = _state(ctx, db).kind
        if kind != "unmapped":
            raise ToolError("This course has a mapping. Candidates are proposed only for unmapped courses.")
        if ctx.proposal is not None:
            raise ToolError("Candidates were already proposed for this row.")
        s = get_settings()
        notes = [n["text"] for n in (_state_facts(ctx) or {}).get("_notes", [])]
        res = await run_case(ctx.provider, ctx.repo, ctx.retriever, ctx.source_course_id, ctx.precedent, s.matcher_max_tool_rounds, s.reviewer_max_tool_rounds,
                             note=" | ".join(notes)[:600] or None)
        ctx.proposal = res
        m, r = res.match.output, res.review
        return {"matcher_status": res.match.status, "candidate": m.target_course_id if m else None, "evidence_strength": m.evidence_strength if m else None,
                "missing_or_weaker": (m.missing_or_weaker_outcomes if m else [])[:4], "reviewer_verdict": r.status if r else None,
                "objections": [o.text[:240] for o in (r.output.objections if r and r.output else [])][:3],
                "title_only_suggestions": [t.target_course_id for t in (m.title_only_suggestions if m else [])]}


class DraftRequestArgs(BaseModel):
    what: str = Field(min_length=3, max_length=400, description="Exactly what document or fact is needed")
    why: str = Field(min_length=3, max_length=400)
    to: Literal["student", "school", "officer"] = "student"


class DraftRequest(AgentTool):
    name = "draft_request"
    description = "Draft a request for missing evidence. Nothing is sent; the officer sees the draft."
    input_schema = DraftRequestArgs
    roles: ClassVar[set[str]] = {"case"}

    async def execute(self, args: DraftRequestArgs, ctx: CaseContext):
        with app_session() as db:
            st = _state(ctx, db)
            reqs = list(st.requests or [])
            same = next((r for r in reqs if r["status"] == "DRAFT" and r["what"].strip().lower() == args.what.strip().lower()), None)
            if same:
                return {"request_id": same["id"], "note": "An identical draft already exists."}
            req = {"id": uuid.uuid4().hex[:8], "what": args.what, "why": args.why, "to": args.to, "status": "DRAFT", "created_by": "case_agent"}
            mp.save_state(db, ctx.app_id, ctx.row_id, requests=[*reqs, req])
            return {"request_id": req["id"], "status": "DRAFT"}


class SetRowStateArgs(BaseModel):
    status: Literal["Conditional", "No mapping", "Waiting on document", "Mapped"]
    reason: str = Field(min_length=3, max_length=300)


class SetRowState(AgentTool):
    name = "set_row_state"
    description = "Set this row's state. 'Waiting on document' needs at least one open drafted request. 'Mapped' is refused unless code already says Mapped."
    input_schema = SetRowStateArgs
    roles: ClassVar[set[str]] = {"case"}

    async def execute(self, args: SetRowStateArgs, ctx: CaseContext):
        with app_session() as db:
            st = _state(ctx, db)
            open_reqs = [r for r in (st.requests or []) if r["status"] in ("DRAFT", "SENT")]
            if args.status == "Waiting on document" and not open_reqs:
                raise ToolError("Draft a request first: 'Waiting on document' needs an open request.")
            if args.status == "Mapped":
                raise ToolError("Only the code lookup can mark a row Mapped (condition met and no rule failure). Keep it Conditional or No mapping.")
            if args.status == "No mapping" and st.kind != "unmapped":
                raise ToolError("This course has a mapping, so it cannot be 'No mapping'. Use 'Conditional'.")
            if args.status == "Conditional" and st.kind == "unmapped":
                raise ToolError("This course has no mapping, so it cannot be 'Conditional'. Use 'No mapping'.")
            mp.save_state(db, ctx.app_id, ctx.row_id, status=args.status, reason=args.reason)
            return {"status": args.status}


CASE_TOOLS: list[AgentTool] = [ReadMapping(), ReadSourceExcerpts(), ReadTargetExcerpts(), CheckPolicy(), ProposeCandidates(), DraftRequest(), SetRowState()]
