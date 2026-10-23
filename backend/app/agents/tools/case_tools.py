"""Tools for the Case agent. Reads are free; the only writes are a drafted request and the row's state, both guarded by code."""
import uuid
from dataclasses import dataclass
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from ...config import get_settings
from ...db.models import Application, RowState
from ...db.session import app_session
from ...providers.base import LLMProvider
from ...services import doubts
from ...services import mapping as mp
from ...services import student_record as sr
from ..orchestrator import CaseResult, run_case
from .base import AgentTool, LoggedRegistry, ToolContext, ToolError
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


class TracedRegistry(LoggedRegistry):
    """Every tool call the Case agent makes goes to the trace shown in the activity drawer and to the usage ledger."""
    agent = "case_agent"

    def scope(self, ctx, call):
        rid = call.arguments.get("row_id") if isinstance(call.arguments, dict) else None
        return ctx.app_id, rid or ctx.row_id


class OptionalRowArgs(BaseModel):
    row_id: str | None = Field(default=None, description="Leave empty for the row you are working on")


def _row(db, ctx: CaseContext, row_id: str | None):
    """(application, row dict, row state) for the given row id, or for the row this run is about."""
    app = db.get(Application, ctx.app_id)
    rid = row_id or ctx.row_id
    row = next((r for r in app.rows if r["id"] == rid), None)
    st = mp.get_state(db, ctx.app_id, rid) if row else None
    if row is None or st is None:
        raise ToolError(f"Unknown row '{rid}'. Call list_rows for the row ids.")
    return app, row, st


class ReadMapping(AgentTool):
    name = "read_mapping"
    description = "Read a row's mapping, its condition, the code's condition verdict, missing evidence, facts on file, requests already drafted, its program classification and doubts."
    input_schema = OptionalRowArgs
    roles: ClassVar[set[str]] = {"case"}

    async def execute(self, args: OptionalRowArgs, ctx: CaseContext):
        with app_session() as db:
            _app, row, st = _row(db, ctx, args.row_id)
            return {"row_status": st.status, "kind": st.kind, "reason": st.reason, "mapping": st.mapping, "condition_result": st.condition_result,
                    "facts_on_file": {k: v for k, v in (st.facts or {}).items() if not k.startswith("_")}, "officer_notes": (st.facts or {}).get("_notes", []), "requests": st.requests,
                    "grade_on_transcript": row.get("grade"), "program_class": (st.facts or {}).get("_class"), "doubts": (st.facts or {}).get("_doubts", [])}


class ListRows(AgentTool):
    name = "list_rows"
    description = ("List EVERY row of this application, mapped ones included: row id, title, grade, status, mapped AUD course, program class, doubts already raised, and the code's "
                   "signals (stale mapping, near-miss grade, program mismatch). Also the student's program and the free-elective credits left.")
    input_schema = EmptyArgs
    roles: ClassVar[set[str]] = {"case"}

    async def execute(self, args, ctx: CaseContext):
        with app_session() as db:
            app = db.get(Application, ctx.app_id)
            rows = []
            for r in app.rows:
                st = mp.get_state(db, app.id, r["id"])
                if st is None or st.status == mp.NOT_EVAL:
                    continue
                m, cls = st.mapping or {}, (st.facts or {}).get("_class")
                sig = {k: v for k, v in doubts.signals(db, ctx.repo, app, r, st).items() if v}
                rows.append({"row_id": r["id"], "title": r.get("title"), "grade": r.get("grade"), "status": st.status, "aud_code": m.get("aud_code"),
                             "mapping_version": m.get("version"), "program_class": cls["value"] if cls else None, "class_by": cls["by"] if cls else None,
                             "doubts": [d["kind"] for d in (st.facts or {}).get("_doubts", [])], "signals": sig})
            left = sr.allowance_left(app.program, doubts.elective_credits_used(db, app))
            return {"program": app.program, "entry_term": ((app.extraction or {}).get("student_record") or {}).get("term"), "free_elective_credits_left": left, "rows": rows}


class RaiseDoubtArgs(BaseModel):
    row_id: str
    kind: Literal["stale_mapping", "near_miss_grade", "program_mismatch", "other"]
    reason: str = Field(min_length=5, max_length=300)


class RaiseDoubt(AgentTool):
    name = "raise_doubt"
    description = ("Raise a doubt on a row (also a Mapped one) for the officer: stale_mapping, near_miss_grade or program_mismatch. Code checks the doubt against the facts and refuses one it "
                   "cannot corroborate. A doubt never changes the row's status, but the row can then no longer be confirmed in bulk. Kind 'other' is recorded as your unverified note.")
    input_schema = RaiseDoubtArgs
    roles: ClassVar[set[str]] = {"case"}

    async def execute(self, args: RaiseDoubtArgs, ctx: CaseContext):
        with app_session() as db:
            app, row, st = _row(db, ctx, args.row_id)
            evidence = None
            if args.kind != "other":
                evidence = doubts.signals(db, ctx.repo, app, row, st)[args.kind]
                if not evidence:
                    raise ToolError(f"Code does not corroborate a {args.kind} doubt on this row. Call list_rows and look at its signals.")
            existing = list((st.facts or {}).get("_doubts", []))
            if any(d["kind"] == args.kind for d in existing):
                return {"note": "This doubt is already recorded."}
            mp.merge_facts(db, app.id, row["id"], _doubts=[*existing, {"kind": args.kind, "reason": args.reason, "verified": evidence is not None, "evidence": evidence or "", "by": "case_agent"}])
            return {"recorded": True, "verified_by_code": evidence is not None}


class SetClassificationArgs(BaseModel):
    row_id: str
    why: str = Field(min_length=5, max_length=300, description="Why this out-of-program course fits the free-elective allowance")


class SetClassification(AgentTool):
    name = "set_classification"
    description = ("For a row whose mapped AUD course is 'Not applicable' to the program: judge that it fits the student's free-elective allowance and suggest it as a general elective. "
                   "Code refuses it when the course is in a never-transferable area, above beginner level, or the allowance is used up.")
    input_schema = SetClassificationArgs
    roles: ClassVar[set[str]] = {"case"}

    async def execute(self, args: SetClassificationArgs, ctx: CaseContext):
        with app_session() as db:
            app, row, st = _row(db, ctx, args.row_id)
            why = doubts.can_suggest_elective(db, ctx.repo, app, row["id"], st)
            if why:
                raise ToolError(why)
            code = st.mapping["aud_code"]
            mp.merge_facts(db, app.id, row["id"], _class={"value": sr.ELECTIVE, "why": args.why, "by": "case_agent", "aud_code": code})
            return {"classification": sr.ELECTIVE}


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


CASE_TOOLS: list[AgentTool] = [ReadMapping(), ListRows(), RaiseDoubt(), SetClassification(), ReadSourceExcerpts(), ReadTargetExcerpts(), CheckPolicy(), ProposeCandidates(), DraftRequest(), SetRowState()]
