"""The Intake agent: when a block fails an extraction check, it re-reads only that block (a cropped region, same always-vision reader),
code re-runs the check, and a corrected value is accepted only if the check passes. Whatever still fails is flagged on the exact cell with
one specific question. A small fixed number of re-reads per transcript. Sheets whose blocks pass never reach this agent."""
import asyncio
import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from ..config import get_settings
from ..domain.schemas import AgentOutput
from ..providers.base import LLMProvider, ToolCall
from ..services import extraction_checks as xc
from ..services import grade_checks as gc
from ..services import vision_reader as vr
from ..services.usage import trace
from .loop import run_bounded_agent
from .tools.base import AgentTool, ToolContext, ToolError, ToolRegistry, ToolResult

PROMPT_VERSION = "intake_v2"
PROMPT = (Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.txt").read_text()
FIELDS = {"title": "subject", "grade": "letter", "points": "points", "credits": "credits"}


@dataclass
class IntakeContext(ToolContext):
    app_id: str = ""
    provider: LLMProvider | None = None
    blocks: dict[str, dict[str, Any]] = field(default_factory=dict)  # block key -> stored read result (mutated in place)
    spans: dict[str, tuple[int, tuple[int, int]]] = field(default_factory=dict)  # key -> (page number, vertical span)
    render: Any = None  # callable(page_no, dpi) -> PIL image
    scale: dict[str, Any] = field(default_factory=dict)
    budget: int = 3
    changes: list[dict[str, Any]] = field(default_factory=list)
    questions: int = 0


def accept_failed_block(new: dict[str, Any], scale: dict[str, Any]) -> dict[str, Any] | None:
    """A block that failed outright is accepted from a re-read only if it now yields course rows and every one of them passes its checks."""
    rows = [r for r in new.get("rows", []) if not xc.is_non_course(r)]
    return new if rows and not any(gc.check_row(r, scale).flags for r in rows) else None


def accept_rechecked(old: dict[str, Any], new: dict[str, Any], scale: dict[str, Any], prior: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Take a value from the new reading only for a row whose checks now pass (and a total only if it now passes). Returns (block, changes)."""
    merged, changes = copy.deepcopy(old), []
    bad_old = {f["row"] for f in xc.block_failures(old, scale, prior) if f["row"] is not None}
    new_rows = new.get("rows", [])
    for i in bad_old:
        o = old["rows"][i]
        cand = next((j for j, r in enumerate(new_rows) if (r.get("subject") or "").strip().lower() == (o.get("subject") or "").strip().lower()), None)
        if cand is None and len(new_rows) == len(old["rows"]):
            cand = i
        if cand is None or gc.check_row(new_rows[cand], scale).flags:
            continue  # the check still fails for this row: keep the old value
        for k in ("subject", "mark", "letter", "points", "credits"):
            if new_rows[cand].get(k) != o.get(k):
                changes.append({"row": o.get("subject"), "field": k, "from": o.get(k), "to": new_rows[cand].get(k)})
        merged["rows"][i] = {**new_rows[cand]}
    if any(f["row"] is None for f in xc.block_failures(old, scale, prior)) and not any(f["row"] is None for f in xc.block_failures(new, scale, prior)):
        for k, v in new.get("totals", {}).items():
            if v != old.get("totals", {}).get(k):
                changes.append({"row": "(totals)", "field": k, "from": old.get("totals", {}).get(k), "to": v})
        merged["totals"] = new.get("totals", merged.get("totals", {}))
    for r in merged["rows"]:  # refresh the per-row flags from the checks
        c = gc.check_row(r, scale)
        r["flags"], r["uncertain"] = c.flags, c.uncertain
    merged["flags"] = [f"{(r.get('subject') or '?')[:30]}: {f}" for r in merged["rows"] for f in r["flags"]] + [f["detail"] for f in xc.block_failures(merged, scale, prior) if f["row"] is None]
    return merged, changes


class KeyArgs(BaseModel):
    block: str


class GetBlockFailures(AgentTool):
    name = "get_block_failures"
    description = "List the failed extraction checks for one block: which row and cell, and what the check found."
    input_schema = KeyArgs
    roles: ClassVar[set[str]] = {"intake"}

    async def execute(self, args: KeyArgs, ctx: IntakeContext):
        if args.block not in ctx.blocks:
            raise ToolError(f"Unknown block '{args.block}'.")
        return [f for f in xc.cell_failures(ctx.blocks, ctx.scale) if args.block in (f["block"], f["via"])]


class RereadArgs(BaseModel):
    block: str
    focus: str = Field(min_length=3, max_length=200, description="The cell to look at, e.g. 'the letter grade of Geography'")


class RereadBlock(AgentTool):
    name = "reread_block"
    description = "Re-read ONE failing block (a cropped region) at higher resolution. Code re-runs the checks and keeps only values whose check now passes."
    input_schema = RereadArgs
    roles: ClassVar[set[str]] = {"intake"}

    async def execute(self, args: RereadArgs, ctx: IntakeContext):
        if args.block not in ctx.blocks:
            raise ToolError(f"Unknown block '{args.block}'.")
        if not xc.failures_in(ctx.blocks, args.block, ctx.scale):
            raise ToolError("This block has no failing check; there is nothing to re-read.")
        if ctx.budget <= 0:
            raise ToolError("The re-read budget for this transcript is used up. Flag the remaining cells instead.")
        ctx.budget -= 1
        page, span = ctx.spans[args.block]
        dpi = get_settings().vision_recheck_dpi
        img = await asyncio.to_thread(ctx.render, page, dpi)
        b64 = vr.crop_png_b64(img, span, dpi / get_settings().vision_dpi)
        new = vr.check_block(await vr.read_block(ctx.provider, b64, args.block, hint=args.focus), ctx.scale)
        if ctx.blocks[args.block].get("failed"):
            ok = accept_failed_block(new, ctx.scale)
            merged = {**ok, "reread": True, "page": page} if ok else ctx.blocks[args.block]
            changes = [{"row": "(block)", "field": "block", "from": "unreadable", "to": f"{len(ok['rows'])} rows read"}] if ok else []
        else:
            merged, changes = accept_rechecked(ctx.blocks[args.block], new, ctx.scale, xc.prior_rows(ctx.blocks, args.block))
        ctx.blocks[args.block].clear()
        ctx.blocks[args.block].update(merged)
        ctx.changes += [{"block": args.block, **c} for c in changes]
        left = xc.failures_in(ctx.blocks, args.block, ctx.scale)
        return {"accepted_changes": changes, "still_failing": left, "rechecks_left": ctx.budget}


class FlagCellArgs(BaseModel):
    block: str
    row: int = Field(ge=0)
    field: str
    question: str = Field(min_length=5, max_length=240)


class FlagCell(AgentTool):
    name = "flag_cell"
    description = "Flag one still-failing cell for the officer with ONE specific question. Allowed only for a cell that still fails its check."
    input_schema = FlagCellArgs
    roles: ClassVar[set[str]] = {"intake"}

    async def execute(self, args: FlagCellArgs, ctx: IntakeContext):
        b = ctx.blocks.get(args.block)
        if b is None:
            raise ToolError(f"Unknown block '{args.block}'.")
        if not any(f["block"] == args.block and f["row"] == args.row and f["field"] == args.field for f in xc.cell_failures(ctx.blocks, ctx.scale)):
            raise ToolError("That cell does not fail a check, so it cannot be flagged.")
        attach_question(b, args.row, args.field, args.question)
        ctx.questions += 1
        return {"flagged": True}


def attach_question(block: dict[str, Any], row: int, field_: str, question: str) -> None:
    qs = block["rows"][row].setdefault("questions", [])
    if not any(q["field"] == field_ for q in qs):
        qs.append({"field": field_, "question": question})


class IntakeReport(AgentOutput):
    blocks_checked: list[str] = Field(default_factory=list)
    note: str = ""


class TracedIntakeRegistry(ToolRegistry):
    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolResult:
        res = await super().execute(call, ctx)
        if isinstance(ctx, IntakeContext):
            trace(ctx.app_id, "", "intake_agent", call.name, call.arguments, res.ok, (res.error or res.to_message())[:300])
        return res


def template_questions(ctx: IntakeContext) -> int:
    """Whatever still fails and was not flagged by the agent gets one specific question from code, so no failing cell is left unexplained."""
    n = 0
    for f in xc.cell_failures(ctx.blocks, ctx.scale):
        if f["row"] is None:
            continue  # an unreadable block has no cell; the reading screen already says a part could not be read
        b = ctx.blocks[f["block"]]
        if not any(q["field"] == f["field"] for q in b["rows"][f["row"]].get("questions", [])):
            attach_question(b, f["row"], f["field"], xc.question_for(f))
            n += 1
    return n


async def run_intake_agent(provider: LLMProvider, repo, retriever, app_id: str, blocks: dict[str, dict[str, Any]], spans: dict[str, Any], render, scale: dict[str, Any],
                           budget: int) -> IntakeContext:
    ctx = IntakeContext(repo, retriever, "intake", app_id, app_id=app_id, provider=provider, blocks=blocks, spans=spans, render=render, scale=scale, budget=budget)
    failing = {k: [f"{f['field']}: {f['detail']}" for f in xc.failures_in(blocks, k, scale)] for k in blocks if xc.failures_in(blocks, k, scale)}
    if failing:
        import json
        try:
            await run_bounded_agent(provider, TracedIntakeRegistry([GetBlockFailures(), RereadBlock(), FlagCell()]), ctx, system=PROMPT,
                                    user=json.dumps({"failing_blocks": failing, "rechecks_available": budget}), final_model=IntakeReport,
                                    max_tool_rounds=get_settings().intake_max_tool_rounds, stage="intake")
        except Exception as e:  # noqa: BLE001  the recheck is an extra: if it fails, reading continues and code flags the cells itself
            trace(app_id, "", "intake_agent", "intake_failed", {}, False, f"{type(e).__name__}: {str(e)[:120]}")
    n = template_questions(ctx)
    trace(app_id, "", "code", "intake_summary", {"changes": len(ctx.changes), "agent_questions": ctx.questions, "code_questions": n, "rechecks_used": budget - ctx.budget})
    return ctx
