"""The Auditor agent: reviews the sheet on EVERY export against the officer's decisions, the transcript, the mapping records and the program plan.
Tools (code): run_sheet_checks, diff_sheet_vs_decisions, read_transcript_row, read_mapping, propose_fix. Code re-checks every proposed fix on a copy
of the sheet and refuses one that does not clear the row's failures or breaks something else. Every explanation shown to the officer is built by code
from the verified difference and check results, never from the agent's own words. Nothing applies until the officer clicks Apply, so confirmed
decisions never change silently."""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from ..config import get_settings
from ..db.models import Application, CourseMapping
from ..db.session import app_session
from ..domain.schemas import AgentOutput
from ..providers.base import LLMProvider
from ..services import actions as ac
from ..services import mapping as mp
from ..services import sheet as sh
from .loop import run_bounded_agent
from .tools.base import AgentTool, LoggedRegistry, ToolContext, ToolError
from .tools.evidence_tools import EmptyArgs

PROMPT_VERSION = "auditor_v2"
PROMPT = (Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.txt").read_text()


@dataclass
class AuditContext(ToolContext):
    app_id: str = ""
    proposals: dict[str, dict[str, Any]] = field(default_factory=dict)  # row_id -> verified proposal


def _sheet(ctx: AuditContext, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    with app_session() as db:
        return sh.build(db, db.get(Application, ctx.app_id), overrides)


def _row(ctx: AuditContext, row_id: str) -> dict[str, Any]:
    with app_session() as db:
        r = next((r for r in db.get(Application, ctx.app_id).rows if r["id"] == row_id), None)
    if r is None:
        raise ToolError(f"Unknown row '{row_id}'.")
    return r


class RunSheetChecks(AgentTool):
    name = "run_sheet_checks"
    description = "Run the deterministic sheet checks (mapping record, credits, policy, unresolved-but-confirmed, duplicates) and the program-plan warnings. Returns what fails."
    input_schema = EmptyArgs
    roles: ClassVar[set[str]] = {"auditor"}

    async def execute(self, args, ctx: AuditContext):
        s = _sheet(ctx)
        return {"ok": s["ok"], "checks": s["checks"], "warnings": s["warnings"], "lines": len(s["rows"])}


class DiffSheetVsDecisions(AgentTool):
    name = "diff_sheet_vs_decisions"
    description = "Every sheet cell (AUD course or credits) that differs from what the officer decided for that row, with the decision's action, who made it and when."
    input_schema = EmptyArgs
    roles: ClassVar[set[str]] = {"auditor"}

    async def execute(self, args, ctx: AuditContext):
        return {"differences": _sheet(ctx)["diff"]}


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


class ReadMapping(AgentTool):
    name = "read_mapping"
    description = "The live mapping record for a row, the row's state, its program classification, the officer's action and the sheet line as it stands."
    input_schema = RowArgs
    roles: ClassVar[set[str]] = {"auditor"}

    async def execute(self, args: RowArgs, ctx: AuditContext):
        with app_session() as db:
            st = mp.get_state(db, ctx.app_id, args.row_id)
            if st is None:
                raise ToolError("This row has no state.")
            live = db.get(CourseMapping, st.mapping["id"]) if st.mapping and st.mapping.get("id") else None
            act = ac.latest(db, ctx.app_id).get(args.row_id)
            line = next((ln for ln in sh.build(db, db.get(Application, ctx.app_id))["rows"] if ln["row_id"] == args.row_id), None)
            return {"state": st.status, "kind": st.kind, "mapping_snapshot": st.mapping, "live_mapping": mp.mapping_dict(live) if live else None, "program_class": (st.facts or {}).get("_class"),
                    "doubts": (st.facts or {}).get("_doubts", []), "officer_action": ac.action_dict(act) if act else None, "sheet_line": line}


class ProposeFixArgs(BaseModel):
    row_id: str
    aud_code: str | None = None
    credits: int | None = Field(default=None, ge=1, le=12)
    why: str = Field(min_length=5, max_length=300)


class ProposeFix(AgentTool):
    name = "propose_fix"
    description = ("Propose a corrected AUD course and/or credits for ONE row that fails a check or differs from the officer's decision. Code re-runs the checks on a copy of the sheet "
                   "and refuses the fix unless the row's failures are gone and nothing new fails. The officer applies it after seeing before and after.")
    input_schema = ProposeFixArgs
    roles: ClassVar[set[str]] = {"auditor"}

    async def execute(self, args: ProposeFixArgs, ctx: AuditContext):
        if args.aud_code is None and args.credits is None:
            raise ToolError("Propose an AUD course or credits.")
        row = _row(ctx, args.row_id)
        cur = _sheet(ctx)
        ln = next((x for x in cur["rows"] if x["row_id"] == args.row_id), None)
        if ln is None:
            raise ToolError("This row is not on the sheet.")
        failing = {c["row_id"] for c in cur["checks"]} | {d["row_id"] for d in cur["diff"]}
        if args.row_id not in failing:
            raise ToolError("This row has no failed check and no difference from the officer's decision, so there is nothing to fix.")
        if args.aud_code and args.aud_code not in ctx.repo.targets:
            raise ToolError(f"{args.aud_code} is not an AUD course.")
        if args.aud_code and (fails := mp.policy_fails(row.get("grade"), args.aud_code, ctx.repo)):
            raise ToolError("That course is blocked by policy: " + "; ".join(fails))
        with app_session() as db:
            act = ac.latest(db, ctx.app_id).get(args.row_id)
            st = mp.get_state(db, ctx.app_id, args.row_id)
            live = db.get(CourseMapping, st.mapping["id"]) if st and st.mapping and st.mapping.get("id") else None
        # The referee: a fix may only restore what the records say. A plain confirmation follows the live mapping record; a changed row follows the officer's own choice.
        if act is not None and act.action in ("CHANGE", "CORRECT"):
            ref, ref_name = {"aud_code": act.chosen.get("aud_code"), "credits": act.chosen.get("credits")}, "the officer's recorded choice"
        elif live is not None and live.status == "ACTIVE":
            ref, ref_name = {"aud_code": live.aud_code, "credits": live.aud_credits}, "the live mapping record"
        else:
            raise ToolError("There is no active mapping record or recorded choice to restore for this row; it needs the officer's judgment.")
        to = {"aud_code": args.aud_code or ref["aud_code"], "credits": args.credits if args.credits is not None else (ref["credits"] or ac.transfer_credits())}
        if to != ref:
            raise ToolError(f"A fix may only restore {ref_name} ({ref['aud_code']}, {ref['credits']} credits); you proposed {to['aud_code']}, {to['credits']} credits. Code does not hold this fix.")
        trial = {**{k: v["to"] for k, v in ctx.proposals.items()}, args.row_id: to}
        after = _sheet(ctx, trial)
        mine = [c for c in after["checks"] if c["row_id"] == args.row_id]
        new = [c for c in after["checks"] if c["row_id"] != args.row_id and c not in cur["checks"]]
        if mine or new:
            raise ToolError("Code re-checked this fix and it does not hold: " + "; ".join(c["message"] for c in [*mine, *new])[:300])
        dec = ln["decision"]
        ctx.proposals[args.row_id] = {"row_id": args.row_id, "source_course": ln["source_course"], "why": args.why, "from": {"aud_code": ln["aud_code"], "credits": ln["aud_credits"]}, "to": to,
                                      "touches_confirmed": bool(act and act.action in ac.RESOLVING),
                                      "restores_decision": to["aud_code"] == dec["aud_code"] and (dec["aud_credits"] is None or to["credits"] == dec["aud_credits"]),
                                      "rechecked": True}
        return {"recorded": True, "rechecked": "every check passes with this fix"}


class AuditReport(AgentOutput):
    summary: str = ""


class AuditorRegistry(LoggedRegistry):
    agent = "auditor_agent"


def explain(before: dict[str, Any], proposals: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """One explanation per affected row, built ONLY from the verified differences, the failed checks and the re-checked proposals."""
    rows: dict[str, dict[str, Any]] = {}
    for d in before["diff"]:
        rows.setdefault(d["row_id"], {"course": d["source_course"], "diff": [], "checks": []})["diff"].append(d)
    for c in before["checks"]:
        rows.setdefault(c["row_id"], {"course": c["source_course"], "diff": [], "checks": []})["checks"].append(c)
    out = []
    for rid, r in rows.items():
        parts = []
        for d in r["diff"]:
            fld = "AUD course" if d["field"] == "aud_code" else "AUD credits"
            who = f" ({d['action'].lower()} by {d['by']})" if d["by"] else ""
            parts.append(f"{r['course']}: the sheet shows {fld} {d['sheet']}, but the recorded decision{who} says {d['decision']}.")
        parts += [f"Check '{c['check']}' fails: {c['message']}." for c in r["checks"]]
        p = proposals.get(rid)
        parts.append(f"Proposed fix: {p['from']['aud_code']}/{p['from']['credits']} to {p['to']['aud_code']}/{p['to']['credits']}; the checks pass with it." if p
                     else "No fix was proposed; this needs your judgment.")
        out.append({"row_id": rid, "explanation": " ".join(parts)})
    return out


async def run_auditor(provider: LLMProvider, repo, retriever, app_id: str) -> dict[str, Any]:
    """Runs on every export. Returns the report stored for the officer (also when the sheet is clean)."""
    ctx = AuditContext(repo, retriever, "auditor", app_id, app_id=app_id)
    before = _sheet(ctx)
    tools = [RunSheetChecks(), DiffSheetVsDecisions(), ReadTranscriptRow(), ReadMapping(), ProposeFix()]
    s = get_settings()
    out = await run_bounded_agent(provider, AuditorRegistry(tools), ctx, system=PROMPT, user=json.dumps({"sheet_hash": before["sheet_hash"], "lines": len(before["rows"])}),
                                  final_model=AuditReport, max_tool_rounds=s.auditor_max_tool_rounds, stage="audit", max_seconds=s.auditor_max_seconds)
    after = _sheet(ctx, {k: v["to"] for k, v in ctx.proposals.items()})
    proposals = [{**p, "id": p["row_id"]} for p in ctx.proposals.values()]
    explanations = explain(before, ctx.proposals)
    clean = before["ok"] and not before["diff"]
    summary = ("The sheet passes every check and matches the recorded decisions." if clean
               else f"{len(before['checks'])} check(s) fail and {len(before['diff'])} cell(s) differ from the recorded decisions; {len(proposals)} fix(es) proposed and re-checked.")
    return {"status": "OK" if out.status == "OK" else "FAILED", "clean": clean, "sheet_hash": before["sheet_hash"], "summary": summary,
            "agent_comment": out.output.summary if out.output else "", "explanations": explanations, "warnings": before["warnings"], "differences": before["diff"],
            "proposals": proposals, "checks_before": before["checks"], "checks_after": after["checks"], "passes_after": after["ok"], "applied": [],
            "prompt_version": PROMPT_VERSION, "flags": list(out.flags) + ([] if out.status == "OK" else [out.error or "AGENT_FAILED"])}
