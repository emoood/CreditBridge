"""The Auditor agent: runs ONLY when a deterministic sheet check fails. It reads the transcript row, the mapping record and the policy result,
explains the discrepancy and PROPOSES a correction; code re-runs the checks on a corrected copy of the sheet. Nothing is applied until the
officer sees the before and after and clicks, so officer-confirmed decisions never change silently. Sheets that pass never call a model."""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from ..config import get_settings
from ..db.models import CourseMapping
from ..db.session import app_session
from ..domain.schemas import AgentOutput
from ..providers.base import LLMProvider, ToolCall
from ..services import actions as ac
from ..services import mapping as mp
from ..services import sheet as sh
from ..services.usage import trace
from .loop import run_bounded_agent
from .tools.base import AgentTool, ToolContext, ToolError, ToolRegistry, ToolResult
from .tools.evidence_tools import EmptyArgs

PROMPT_VERSION = "auditor_v1"
PROMPT = (Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.txt").read_text()


@dataclass
class AuditContext(ToolContext):
    app_id: str = ""
    proposals: dict[str, dict[str, Any]] = field(default_factory=dict)  # row_id -> proposal


def _sheet(ctx: AuditContext, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    from ..db.models import Application
    with app_session() as db:
        return sh.build(db, db.get(Application, ctx.app_id), overrides)


def _row(ctx: AuditContext, row_id: str) -> dict[str, Any]:
    from ..db.models import Application
    with app_session() as db:
        r = next((r for r in db.get(Application, ctx.app_id).rows if r["id"] == row_id), None)
    if r is None:
        raise ToolError(f"Unknown row '{row_id}'.")
    return r


class ListFailures(AgentTool):
    name = "list_failures"
    description = "List every failed sheet check: the row, which check, and the message."
    input_schema = EmptyArgs
    roles: ClassVar[set[str]] = {"auditor"}

    async def execute(self, args, ctx: AuditContext):
        return _sheet(ctx)["checks"]


class RowArgs(BaseModel):
    row_id: str


class ReadTranscriptRow(AgentTool):
    name = "read_transcript_row"
    description = "The original transcript row as read and confirmed: course title, grade, credits, term and the linked curriculum subject."
    input_schema = RowArgs
    roles: ClassVar[set[str]] = {"auditor"}

    async def execute(self, args: RowArgs, ctx: AuditContext):
        r = _row(ctx, args.row_id)
        return {k: r.get(k) for k in ("title", "grade", "mark", "credits", "term", "subject")}


class ReadMappingRecord(AgentTool):
    name = "read_mapping_record"
    description = "The live mapping record for this row, the row's state, and the sheet line as it stands (with the officer's action, if any)."
    input_schema = RowArgs
    roles: ClassVar[set[str]] = {"auditor"}

    async def execute(self, args: RowArgs, ctx: AuditContext):
        from ..db.models import Application
        with app_session() as db:
            st = mp.get_state(db, ctx.app_id, args.row_id)
            if st is None:
                raise ToolError("This row has no state.")
            live = db.get(CourseMapping, st.mapping["id"]) if st.mapping and st.mapping.get("id") else None
            act = ac.latest(db, ctx.app_id).get(args.row_id)
            line = next((ln for ln in sh.build(db, db.get(Application, ctx.app_id))["rows"] if ln["row_id"] == args.row_id), None)
            return {"state": st.status, "kind": st.kind, "mapping_snapshot": st.mapping, "live_mapping": mp.mapping_dict(live) if live else None,
                    "officer_action": ac.action_dict(act) if act else None, "sheet_line": line}


class ReadPolicyResult(AgentTool):
    name = "read_policy_result"
    description = "The deterministic policy failures for this row's grade against the AUD course on its sheet line."
    input_schema = RowArgs
    roles: ClassVar[set[str]] = {"auditor"}

    async def execute(self, args: RowArgs, ctx: AuditContext):
        ln = next((ln for ln in _sheet(ctx)["rows"] if ln["row_id"] == args.row_id), None)
        if not ln or not ln["aud_code"]:
            return {"policy_failures": [], "note": "This sheet line has no AUD course."}
        return {"aud_code": ln["aud_code"], "policy_failures": mp.policy_fails(_row(ctx, args.row_id).get("grade"), ln["aud_code"], ctx.repo)}


class ProposeArgs(BaseModel):
    row_id: str
    aud_code: str | None = None
    credits: int | None = Field(default=None, ge=1, le=12)
    why: str = Field(min_length=5, max_length=300)


class ProposeCorrection(AgentTool):
    name = "propose_correction"
    description = "Propose a corrected AUD course and/or credits for one row. It is only a proposal: the officer applies it after seeing before and after."
    input_schema = ProposeArgs
    roles: ClassVar[set[str]] = {"auditor"}

    async def execute(self, args: ProposeArgs, ctx: AuditContext):
        if args.aud_code is None and args.credits is None:
            raise ToolError("Propose an AUD course or credits.")
        row = _row(ctx, args.row_id)
        sheet_rows = {ln["row_id"]: ln for ln in _sheet(ctx)["rows"]}
        ln = sheet_rows.get(args.row_id)
        if ln is None:
            raise ToolError("This row is not on the sheet.")
        if args.aud_code and args.aud_code not in ctx.repo.targets:
            raise ToolError(f"{args.aud_code} is not an AUD course.")
        if args.aud_code and (fails := mp.policy_fails(row.get("grade"), args.aud_code, ctx.repo)):
            raise ToolError("That course is blocked by policy: " + "; ".join(fails))
        with app_session() as db:
            act = ac.latest(db, ctx.app_id).get(args.row_id)
        ctx.proposals[args.row_id] = {"row_id": args.row_id, "source_course": ln["source_course"], "why": args.why,
                                      "from": {"aud_code": ln["aud_code"], "credits": ln["aud_credits"]},
                                      "to": {"aud_code": args.aud_code or ln["aud_code"], "credits": args.credits if args.credits is not None else (ln["aud_credits"] or ac.transfer_credits())},
                                      "touches_confirmed": bool(act and act.action in ac.RESOLVING)}
        return {"recorded": True}


class RerunChecks(AgentTool):
    name = "rerun_checks"
    description = "Apply your proposals to a COPY of the sheet and re-run every check. Returns what still fails."
    input_schema = EmptyArgs
    roles: ClassVar[set[str]] = {"auditor"}

    async def execute(self, args, ctx: AuditContext):
        res = _sheet(ctx, {k: v["to"] for k, v in ctx.proposals.items()})
        return {"ok": res["ok"], "still_failing": res["checks"]}


class AuditExplanation(AgentOutput):
    row_id: str
    explanation: str


class AuditReport(AgentOutput):
    summary: str
    explanations: list[AuditExplanation] = Field(default_factory=list)


class TracedAuditRegistry(ToolRegistry):
    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        res = await super().execute(call, ctx)
        if isinstance(ctx, AuditContext):
            trace(ctx.app_id, call.arguments.get("row_id", "") if isinstance(call.arguments, dict) else "", "auditor_agent", call.name, call.arguments, res.ok, (res.error or res.to_message())[:300])
        return res


async def run_auditor(provider: LLMProvider, repo, retriever, app_id: str) -> dict[str, Any]:
    """Only when a sheet check fails (the caller guarantees it). Returns the report stored for the officer."""
    ctx = AuditContext(repo, retriever, "auditor", app_id, app_id=app_id)
    before = _sheet(ctx)
    if before["ok"]:
        raise ValueError("The sheet passes every check; the Auditor is not needed")
    tools = [ListFailures(), ReadTranscriptRow(), ReadMappingRecord(), ReadPolicyResult(), ProposeCorrection(), RerunChecks()]
    out = await run_bounded_agent(provider, TracedAuditRegistry(tools), ctx, system=PROMPT, user=json.dumps({"failed_checks": before["checks"]}),
                                  final_model=AuditReport, max_tool_rounds=get_settings().auditor_max_tool_rounds, stage="audit")
    after = _sheet(ctx, {k: v["to"] for k, v in ctx.proposals.items()})
    proposals = [{**p, "id": f"{p['row_id']}"} for p in ctx.proposals.values()]
    return {"status": "OK" if out.status == "OK" else "FAILED", "summary": out.output.summary if out.output else "The Auditor could not finish; the failures above still stand.",
            "explanations": [e.model_dump() for e in out.output.explanations] if out.output else [], "proposals": proposals,
            "checks_before": before["checks"], "checks_after": after["checks"], "passes_after": after["ok"], "applied": [], "prompt_version": PROMPT_VERSION}
