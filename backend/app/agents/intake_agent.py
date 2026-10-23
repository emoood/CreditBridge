"""The Intake agent: drives the reading of EVERY uploaded transcript. Code is its tools and its verifier.

  list_pages, view_page, crop_region(zoom), read_block (a vision call), run_checks, flag_cell, submit_courses

The agent plans which pages and blocks to read, reads only those, runs the code checks and chooses its own recovery on a failure (a tighter crop, a higher
zoom, a whole-block re-read; budget: 2 recovery rounds). Vision is the reader: the text layer of a digital PDF is a cross-check only and never overrides
it. An EMPTY reading counts as a failed read. In addition to the code checks the agent does three checks of its own: a visual check (does the value appear
on the page image), a plausibility check (odd grade, repeated course, term out of order) and a name check (transcript name against the student record).
Code makes the final accept-or-reject call on every number: a re-read value is kept only where its check now passes, and the agent cannot type a value.
A block the agent neither read nor legitimately skipped is read by code afterwards, so the table is never short because of the agent."""
import asyncio
import base64
import copy
import difflib
import io
import itertools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

from PIL import Image
from pydantic import BaseModel, Field

from ..config import get_settings
from ..domain.schemas import AgentOutput
from ..providers.base import LLMProvider
from ..services import cross_check as cc
from ..services import extraction_checks as xc
from ..services import grade_checks as gc
from ..services import vision_reader as vr
from ..services.usage import trace
from .loop import run_bounded_agent
from .tools.base import AgentTool, LoggedRegistry, ToolContext, ToolError, WithImages

PROMPT_VERSION = "intake_v3"
PROMPT = (Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.txt").read_text()
SUBMIT = "submit_courses"
FIELD_ALIASES = {"letter": "grade", "mark": "grade", "subject": "title", "course": "title", "name": "title", "gpa": "points", "grade points": "points", "quality points": "points",
                 "credit": "credits", "term total": "credits"}
TOTAL_FIELDS = {"credits", "points"}


@dataclass
class Region:
    parent: str
    page: int
    span: tuple[int, int]
    zoom: float


@dataclass
class IntakeContext(ToolContext):
    app_id: str = ""
    provider: LLMProvider | None = None
    path: str = ""
    scale: dict[str, Any] = field(default_factory=dict)
    spans: dict[str, tuple[int, tuple[int, int]]] = field(default_factory=dict)  # block key -> (page number, vertical span at the base dpi)
    blocks: dict[str, dict[str, Any]] = field(default_factory=dict)  # block key -> stored reading (only blocks read so far)
    skipped: dict[str, str] = field(default_factory=dict)
    xr: dict[str, tuple[int, int]] = field(default_factory=dict)  # block key -> horizontal range, for tables printed side by side (absent = the whole width)
    order: dict[str, tuple] = field(default_factory=dict)  # block key -> reading order (a column's blocks follow each other, top to bottom)
    regions: dict[str, Region] = field(default_factory=dict)
    text: dict[int, list[dict[str, Any]]] | None = None  # the text layer's course rows per page: a cross-check only
    recovery_left: int = 2
    recovery_used: int = 0
    views_left: int = 3
    plant: dict[str, Any] | None = None  # PLANTED FOR DEMO misread switch
    planted: list[dict[str, Any]] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)
    questions: int = 0
    lines: dict[str, int] = field(default_factory=dict)  # text lines per block (from the ink), to question a reading that returned far fewer rows
    imgs: dict[int, list[Image.Image]] = field(default_factory=dict)  # dpi -> rendered pages
    record_name: str | None = None
    name_check: dict[str, Any] = field(default_factory=dict)
    by_code: list[str] = field(default_factory=list)  # blocks code had to read itself
    submitted: dict[str, Any] | None = None

    def page_img(self, page: int, dpi: int) -> Image.Image:
        if dpi not in self.imgs:
            self.imgs[dpi] = vr.render_pages(self.path, dpi)
        return self.imgs[dpi][page - 1]

    def okey(self, k: str) -> tuple:
        return self.order.get(k) or (self.spans[k][0], 0, 0, self.spans[k][1][0])

    def ordered(self) -> dict[str, dict[str, Any]]:
        """Read blocks in reading order (term totals compare a block with the rows of earlier blocks of the SAME table, so the order must be the page's, not the read order)."""
        return {k: self.blocks[k] for k in sorted(self.blocks, key=self.okey) if k in self.blocks}

    def unread(self) -> list[str]:
        return [k for k in sorted(self.spans, key=self.okey) if k not in self.blocks and k not in self.skipped]


# ------------------------------------------------------------------------------------------------------- code checks
def status_of(ctx: IntakeContext, key: str) -> str:
    if key in ctx.skipped:
        return "skipped"
    b = ctx.blocks.get(key)
    if b is None:
        return "unread"
    return ("empty" if b.get("error") == "empty_reading" else "failed") if b.get("failed") else "read"


def is_empty(checked: dict[str, Any]) -> bool:
    t = checked["totals"]
    return not checked["rows"] and not t.get("term") and all(v is None for k, v in t.items() if k != "term")


def failures(ctx: IntakeContext) -> list[dict[str, Any]]:
    """Every failing check as an exact cell: grading scale, term totals (only once every block is read), empty or unreadable blocks, and text-layer disagreements."""
    blocks = ctx.ordered()
    out = xc.cell_failures(blocks, ctx.scale)
    if ctx.unread():  # a term total cannot be judged while a block of the term has not been read
        out = [f for f in out if f["check"] not in xc.TOTAL_FIELD]
    if ctx.text is not None:
        out += cc.compare(blocks, ctx.spans, ctx.text, ctx.path, get_settings().vision_dpi)
    return out


def refresh(b: dict[str, Any], scale: dict[str, Any], prior: list[dict[str, Any]] | None = None) -> None:
    for r in b.get("rows", []):
        c = gc.check_row(r, scale)
        r["flags"], r["uncertain"] = c.flags, c.uncertain
    b["flags"] = [f"{(r.get('subject') or '?')[:30]}: {f}" for r in b.get("rows", []) for f in r["flags"]] + [f["detail"] for f in xc.block_failures(b, scale, prior) if f["row"] is None]


def ensure_planted(ctx: IntakeContext) -> None:
    """PLANTED FOR DEMO. Replaces ONE value that really exists (a mark, letter or credits, as configured; the configured subject if it was read, else the first row that
    has such a value) with a wrong one, in a first reading only, before the checks are first shown. A re-read in recovery is never planted."""
    if not ctx.plant or ctx.planted or ctx.unread():
        return
    f = ctx.plant.get("field", "mark")
    cands = [(k, i, r) for k, b in ctx.ordered().items() if not b.get("failed") and not b.get("reread") for i, r in enumerate(b.get("rows", []))
             if r.get(f) is not None and r.get("mark") is not None and not xc.is_non_course(r)]
    pick = next((c for c in cands if ctx.plant["subject"].lower() in (c[2].get("subject") or "").lower()), None) or (cands[0] if cands else None)
    if pick is None:
        return
    k, _i, r = pick
    wrong = str(ctx.plant["wrong"]) if f == "letter" else float(ctx.plant["wrong"])
    rec = {"block": k, "subject": r.get("subject"), "field": f, "from": r[f], "to": wrong}
    r[f] = wrong
    refresh(ctx.blocks[k], ctx.scale, xc.prior_rows(ctx.ordered(), k))
    ctx.planted.append(rec)
    trace(ctx.app_id, "", "code", "planted_for_demo", {**rec, "note": "PLANTED FOR DEMO: one correctly read value was replaced with a wrong one before the extraction checks ran"})


def compact_failures(fs: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    """Cells for the agent. A failed term total is attached by code to every row of its term; the agent sees it once."""
    out, seen_totals = [], set()
    for f in fs:
        if f["check"] in xc.TOTAL_FIELD:
            key = (f["via"], f["check"])
            if key in seen_totals:
                continue
            seen_totals.add(key)
            terms = sorted({x["block"] for x in fs if x["check"] == f["check"] and x["via"] == f["via"]})
            out.append({"check": f["check"], "block": f["via"], "field": "term total", "detail": f["detail"][:110], "rows_of_term_in_blocks": terms})
        else:
            out.append({"check": f["check"], "block": f["block"], "row": f["row"], "field": f["field"], "subject": f.get("subject"), "detail": f["detail"][:110]})
    return out[:limit]


# ----------------------------------------------------------------------------------------------------- reading (tools + code fallback)
async def read_once(ctx: IntakeContext, key: str, span: tuple[int, int], dpi: int, hint: str | None = None) -> dict[str, Any]:
    page = ctx.spans[key][0]
    base = get_settings().vision_dpi
    img = await asyncio.to_thread(ctx.page_img, page, dpi)
    b64 = vr.crop_png_b64(img, span, dpi / base, ctx.xr.get(key))
    reading = await vr.read_block(ctx.provider, b64, key, hint=hint)
    checked = vr.check_block(reading, ctx.scale)
    checked["page"], checked["reread"] = page, False
    checked["empty"] = is_empty(checked)
    return checked


async def code_read_block(ctx: IntakeContext, key: str) -> None:
    """The plain reader (no agent): one reading, and one re-read at a higher resolution if its checks flag something. Used for blocks the agent left unread."""
    s = get_settings()
    best: dict[str, Any] | None = None
    err = ""
    for attempt in (1, 2):
        try:
            dpi = s.vision_dpi if attempt == 1 else s.vision_reread_dpi
            c = await read_once(ctx, key, ctx.spans[key][1], dpi)
            c["reread"] = attempt == 2
            if best is None or len(c["flags"]) < len(best["flags"]):
                best = c
            err = ""
            if not c["empty"] and not vr.needs_reread(best):
                break
        except Exception as e:  # noqa: BLE001  timeout, bad output or model error: try once more, then give up on this block
            err = getattr(e, "code", "model_error")
    if best is None or best["empty"]:
        ctx.blocks[key] = {"rows": [], "flags": [], "totals": {}, "failed": True, "error": "empty_reading" if best else (err or "model_error"), "page": ctx.spans[key][0]}
    else:
        ctx.blocks[key] = best


def accept_failed_block(new: dict[str, Any], scale: dict[str, Any]) -> dict[str, Any] | None:
    """A block that failed outright (or came back empty) is accepted from a re-read only if it now yields course rows and every one of them passes its checks."""
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
    new_totals = {k: v for k, v in new.get("totals", {}).items() if v is not None and k != "term"}
    if any(f["row"] is None for f in xc.block_failures(old, scale, prior)) and new_totals and not any(f["row"] is None for f in xc.block_failures(new, scale, prior)):
        for k, v in new_totals.items():
            if v != old.get("totals", {}).get(k):
                changes.append({"row": "(totals)", "field": k, "from": old.get("totals", {}).get(k), "to": v})
        merged["totals"] = new.get("totals", merged.get("totals", {}))
    refresh(merged, scale, prior)
    return merged, changes


def hint_for(ctx: IntakeContext, key: str, focus: str | None) -> str:
    own = [f for f in failures(ctx) if f["block"] == key or f.get("via") == key]
    if ctx.blocks.get(key, {}).get("error") == "empty_reading":
        return "The previous reading of this block returned no rows at all, but it contains several lines of text. Read every row."
    parts = [f"{f['field']}: {f['detail']}" for f in own[:3]]
    return "; ".join(([focus] if focus else []) + parts)[:300]


# ------------------------------------------------------------------------------------------------------------------ tools
class EmptyArgs(BaseModel):
    pass


class ListPages(AgentTool):
    name = "list_pages"
    description = "List the pages and their blocks: key, size, text lines, status (unread / read / empty / failed / skipped) and, for a file with a text layer, a short preview."
    input_schema = EmptyArgs
    roles: ClassVar[set[str]] = {"intake"}

    async def execute(self, args, ctx: IntakeContext):
        pages: dict[int, list[dict[str, Any]]] = {}
        digital = ctx.text is not None
        doc = None
        if digital:
            import pymupdf
            doc = pymupdf.open(ctx.path)
        dpi = get_settings().vision_dpi
        for key in sorted(ctx.spans, key=ctx.okey):
            pg, (a, b) = ctx.spans[key]
            item: dict[str, Any] = {"key": key, "height_px": b - a, "text_lines": ctx.lines.get(key, 0), "status": status_of(ctx, key)}
            if doc is not None:
                import pymupdf
                words = doc[pg - 1].get_text("text", clip=pymupdf.Rect(0, a * 72 / dpi, doc[pg - 1].rect.width, b * 72 / dpi)).split("\n")
                item["preview"] = " / ".join(w.strip() for w in words if w.strip())[:90]
            pages.setdefault(pg, []).append(item)
        return {"text_layer": digital, "pages": [{"page": p, "blocks": bl} for p, bl in sorted(pages.items())], "recovery_rounds_left": ctx.recovery_left}


class ViewPageArgs(BaseModel):
    page: int = Field(ge=1)
    block: str | None = Field(default=None, description="Block key to look at instead of the whole page")


class ViewPage(AgentTool):
    name = "view_page"
    description = ("Look at a page (or one block) as an image: for the visual check (does the value really appear on the page?) and to read the student's name in the header. "
                   "At most 3 views per transcript.")
    input_schema = ViewPageArgs
    roles: ClassVar[set[str]] = {"intake"}

    async def execute(self, args: ViewPageArgs, ctx: IntakeContext):
        if ctx.views_left <= 0:
            raise ToolError("The page views for this transcript are used up.")
        pages = {p for p, _ in ctx.spans.values()}
        if args.page not in pages:
            raise ToolError(f"There is no page {args.page}; pages are {sorted(pages)}.")
        dpi = get_settings().vision_dpi
        img = await asyncio.to_thread(ctx.page_img, args.page, dpi)
        if args.block:
            if args.block not in ctx.spans:
                raise ToolError(f"Unknown block '{args.block}'.")
            _pg, (a, b) = ctx.spans[args.block]
            x0, x1 = ctx.xr.get(args.block, (0, img.width))
            img = img.crop((x0, a, min(img.width, x1), min(img.height, b)))
        img = img.copy()
        img.thumbnail((1100, 1100))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        ctx.views_left -= 1
        return WithImages({"shown": f"page {args.page}" + (f", block {args.block}" if args.block else ""), "views_left": ctx.views_left}, [base64.b64encode(buf.getvalue()).decode()])


class CropArgs(BaseModel):
    block: str
    top: float = Field(default=0.0, ge=0.0, le=0.95, description="Start of the part to crop, 0 = top of the block")
    bottom: float = Field(default=1.0, gt=0.05, le=1.0, description="End of the part to crop, 1 = bottom of the block")
    zoom: float = Field(default=1.5, ge=1.0, le=3.0)


class CropRegion(AgentTool):
    name = "crop_region"
    description = "Define a tighter crop (part of a block) and/or a higher zoom, for a recovery re-read. Returns a region id to pass to read_block. Costs nothing until it is read."
    input_schema = CropArgs
    roles: ClassVar[set[str]] = {"intake"}

    async def execute(self, args: CropArgs, ctx: IntakeContext):
        if args.block not in ctx.spans:
            raise ToolError(f"Unknown block '{args.block}'.")
        if args.bottom <= args.top:
            raise ToolError("bottom must be greater than top.")
        pg, (a, b) = ctx.spans[args.block]
        rid = f"{args.block}.r{sum(1 for r in ctx.regions.values() if r.parent == args.block) + 1}"
        ctx.regions[rid] = Region(args.block, pg, (int(a + (b - a) * args.top), int(a + (b - a) * args.bottom)), args.zoom)
        return {"region": rid, "covers": f"{args.top:.0%} to {args.bottom:.0%} of {args.block}", "zoom": args.zoom}


class ReadBlockArgs(BaseModel):
    targets: list[str] = Field(min_length=1, max_length=6, description="Block keys (first read) or region ids / block keys already read (recovery)")
    focus: str | None = Field(default=None, max_length=200, description="For a recovery read: the cell to look at")


class ReadBlock(AgentTool):
    name = "read_block"
    description = ("Read blocks with the vision model. A first read of an unread block costs nothing; reading a block or region again is a RECOVERY round (2 per transcript) "
                   "and is allowed only where a check fails or the reading was empty. Code re-runs the checks and keeps a re-read value only where its check now passes.")
    input_schema = ReadBlockArgs
    roles: ClassVar[set[str]] = {"intake"}

    async def execute(self, args: ReadBlockArgs, ctx: IntakeContext):
        s = get_settings()
        results, spent = [], False
        for t in dict.fromkeys(args.targets):
            reg = ctx.regions.get(t)
            key = reg.parent if reg else t
            if key not in ctx.spans:
                results.append({"target": t, "error": f"Unknown block or region '{t}'. Call list_pages."})
                continue
            first = key not in ctx.blocks
            if not first:
                bad = [f for f in failures(ctx) if f["block"] == key or f.get("via") == key] or ([{}] if status_of(ctx, key) in ("empty", "failed") else [])
                if not bad:
                    results.append({"target": t, "error": "This block has no failing check, so there is nothing to recover."})
                    continue
                if ctx.recovery_left <= 0:
                    results.append({"target": t, "error": "The recovery rounds for this transcript are used up. Flag the failing cells instead."})
                    continue
                if status_of(ctx, key) in ("empty", "failed") and reg and (reg.span[1] - reg.span[0]) < 0.9 * (ctx.spans[key][1][1] - ctx.spans[key][1][0]):
                    results.append({"target": t, "error": "A block that failed or came back empty must be re-read whole: use a region that covers all of it (zoom is fine)."})
                    continue
                spent = True
            span = reg.span if reg else ctx.spans[key][1]
            dpi = min(int(s.vision_dpi * reg.zoom), s.vision_recheck_dpi) if reg else (s.vision_dpi if first else s.vision_recheck_dpi)
            try:
                new = await read_once(ctx, key, span, dpi, None if first else hint_for(ctx, key, args.focus))
            except Exception as e:  # noqa: BLE001
                code = getattr(e, "code", "model_error")
                if first:
                    ctx.blocks[key] = {"rows": [], "flags": [], "totals": {}, "failed": True, "error": code, "page": ctx.spans[key][0]}
                results.append({"target": t, "error": f"The vision read failed ({code})."})
                continue
            changes: list[dict[str, Any]] = []
            if first:
                if new["empty"]:
                    ctx.blocks[key] = {"rows": [], "flags": [], "totals": {}, "failed": True, "error": "empty_reading", "page": ctx.spans[key][0]}
                else:
                    ctx.blocks[key] = new
            elif ctx.blocks[key].get("failed"):
                ok = accept_failed_block(new, ctx.scale)
                if ok:
                    ctx.blocks[key] = {**ok, "reread": True, "recovered": True}
                    changes = [{"row": "(block)", "field": "block", "from": "unreadable", "to": f"{len(ok['rows'])} rows read"}]
            else:
                merged, changes = accept_rechecked(ctx.blocks[key], new, ctx.scale, xc.prior_rows(ctx.ordered(), key))
                if changes:
                    merged["reread"], merged["recovered"] = True, True
                ctx.blocks[key].clear()
                ctx.blocks[key].update(merged)
            ctx.changes += [{"block": key, **c} for c in changes]
            n_rows = sum(1 for r in ctx.blocks[key].get("rows", []) if not xc.is_non_course(r))
            results.append({"target": t, "status": status_of(ctx, key), "course_rows": n_rows, "text_lines_in_block": ctx.lines.get(key, 0),
                            "accepted_changes": [f"{c['row']}: {c['field']} {c['from']} -> {c['to']}" for c in changes][:4]})
        if spent:
            ctx.recovery_left -= 1
            ctx.recovery_used += 1
        ensure_planted(ctx)
        out: dict[str, Any] = {"results": results, "recovery_rounds_left": ctx.recovery_left, "unread_blocks": ctx.unread()}
        if not ctx.unread() or spent:
            out["failures_now"] = compact_failures(failures(ctx), 6)
        return out


class RunChecks(AgentTool):
    name = "run_checks"
    description = ("Run the code checks on everything read so far: every failure as an exact cell (block, row, field), empty or failed blocks, blocks not read yet, disagreements with "
                   "the text layer. Also returns the rows read, for your plausibility check (odd grade, repeated course, term out of order).")
    input_schema = EmptyArgs
    roles: ClassVar[set[str]] = {"intake"}

    async def execute(self, args, ctx: IntakeContext):
        ensure_planted(ctx)
        fs = failures(ctx)
        rows = [f"{k}:{i} {r.get('subject')} | {r.get('letter') or ''} | {r.get('mark') if r.get('mark') is not None else ''} | {r.get('credits') if r.get('credits') is not None else ''} | {r.get('term') or ''}"
                for k, b in ctx.ordered().items() for i, r in enumerate(b.get("rows", [])) if not xc.is_non_course(r)]
        return {"failures": compact_failures(fs, 10), "unread_blocks": ctx.unread(), "rows_read": rows[:30], "recovery_rounds_left": ctx.recovery_left,
                "all_blocks_read": not ctx.unread(), "total_failing_cells": len(fs)}


class FlagCellArgs(BaseModel):
    block: str
    row: int = Field(ge=0)
    field: str = Field(description="grade, points, credits, title or term")
    question: str = Field(min_length=5, max_length=240, description="ONE specific question for the officer")
    kind: Literal["check_failure", "visual", "repeated_course", "term_out_of_order", "odd_grade", "other"] = "check_failure"


class FlagCell(AgentTool):
    name = "flag_cell"
    description = ("Flag one cell for the officer with ONE specific question. kind check_failure is allowed only for a cell that still fails a code check. The other kinds are your own "
                   "checks (visual, repeated_course, term_out_of_order, odd_grade, other): code says whether it could corroborate them; they never change a value.")
    input_schema = FlagCellArgs
    roles: ClassVar[set[str]] = {"intake"}

    async def execute(self, args: FlagCellArgs, ctx: IntakeContext):
        b = ctx.blocks.get(args.block)
        if b is None or args.row >= len(b.get("rows", [])):
            raise ToolError(f"There is no row {args.row} in block '{args.block}'. Call run_checks for the rows read.")
        fld = FIELD_ALIASES.get(args.field.strip().lower(), args.field.strip().lower())
        verified = None
        if args.kind == "check_failure":
            cells = [f for f in failures(ctx) if f["block"] == args.block and f["row"] == args.row]
            if not any(f["field"] == fld or (f["field"] in TOTAL_FIELDS and fld in TOTAL_FIELDS) for f in cells):
                valid = sorted({f"row {f['row']} {f['field']}" for f in failures(ctx) if f["block"] == args.block and f["row"] is not None})
                raise ToolError(f"That cell does not fail a check. Failing cells in {args.block}: {', '.join(valid) or 'none'}. Use another kind for something you noticed yourself.")
            attach_question(b, args.row, fld, args.question, "code_check")
        else:
            verified = corroborate(ctx, args.block, args.row, args.kind)
            attach_question(b, args.row, fld, args.question, "agent_verified" if verified else "agent_unverified")
        ctx.questions += 1
        return {"flagged": True, **({"verified_by_code": bool(verified)} if verified is not None else {})}


def corroborate(ctx: IntakeContext, block: str, row: int, kind: str) -> bool:
    """Can code see what the agent noticed? repeated_course, term_out_of_order and odd_grade are checked; visual and other are the agent's word only."""
    rows = [(k, i, r) for k, b in ctx.ordered().items() for i, r in enumerate(b.get("rows", [])) if not xc.is_non_course(r)]
    me = ctx.blocks[block]["rows"][row]
    if kind == "repeated_course":
        t = re.sub(r"[^a-z0-9]", "", (me.get("subject") or "").lower())
        return bool(t) and sum(1 for _k, _i, r in rows if re.sub(r"[^a-z0-9]", "", (r.get("subject") or "").lower()) == t) > 1
    if kind == "odd_grade":
        g = (me.get("letter") or "").strip().upper()
        return bool(g) and g not in {lt.upper() for _lo, lt, _p in ctx.scale["bands"]}
    if kind == "term_out_of_order":
        order = {"spring": 0, "summer": 1, "fall": 2, "autumn": 2, "winter": 3}

        def key(term: str | None):
            m = re.search(r"(spring|summer|fall|autumn|winter)\D*((?:19|20)\d{2})", (term or "").lower())
            return (int(m.group(2)), order[m.group(1)]) if m else None
        seq = [(key(r.get("term")), (k, i)) for k, i, r in rows if key(r.get("term"))]
        return any(a[0] > b[0] for a, b in itertools.pairwise(seq))
    return False


def attach_question(block: dict[str, Any], row: int, field_: str, question: str, source: str = "code_check") -> None:
    qs = block["rows"][row].setdefault("questions", [])
    if not any(q["field"] == field_ for q in qs):
        qs.append({"field": field_, "question": question if source != "agent_unverified" else f"(Intake agent's observation, not verified by code) {question}", "source": source})


class SkippedBlock(BaseModel):
    block: str
    reason: str = Field(min_length=3, max_length=160)


class IntakeReport(AgentOutput):
    blocks_read: list[str] = Field(default_factory=list)
    skipped: list[SkippedBlock] = Field(default_factory=list)
    name_seen: str | None = Field(default=None, description="The student's name as printed in the header, if you looked at it")
    note: str = ""


class IntakeRegistry(LoggedRegistry):
    agent = "intake_agent"


# ---------------------------------------------------------------------------------------------------------------- finishing
def name_check(record: str | None, seen: str | None, path: str) -> dict[str, Any]:
    """The transcript name against the student record. The agent's reading of the image decides; the text layer is only a cross-check when the agent saw no name."""
    if not record:
        return {"status": "unchecked", "why": "no student record"}
    norm = lambda s: re.sub(r"[^a-z ]", "", (s or "").lower()).split()
    if seen:
        same = set(norm(record)) <= set(norm(seen)) or difflib.SequenceMatcher(None, " ".join(norm(record)), " ".join(norm(seen))).ratio() >= 0.85
        return {"status": "match" if same else "mismatch", "source": "vision (agent)", "seen": seen if not same else None}
    layer = cc.name_in_text(path, record)
    if layer is None:
        return {"status": "unchecked", "why": "the agent did not read the name and the file has no text layer"}
    return {"status": "match" if layer else "mismatch", "source": "text layer (cross-check)"}


def skip_allowed(ctx: IntakeContext, key: str) -> bool:
    """A skip is accepted unless the text layer shows a course row inside that block (then code reads it). Image-only files have no text layer: the skip is accepted
    but shown to the officer."""
    if ctx.text is None:
        return True
    pg, _ = ctx.spans[key]
    return not any(cc.block_of(ctx.path, pg, t["title"], ctx.spans, get_settings().vision_dpi) == key for t in ctx.text.get(pg, []))


async def finish(ctx: IntakeContext) -> None:
    """After the agent (or instead of it): code reads every block that was neither read nor legitimately skipped, shows the checks once more, and gives every
    still-failing cell one specific question, so no failing cell is left unexplained."""
    for key, why in list(ctx.skipped.items()):
        if not skip_allowed(ctx, key):
            del ctx.skipped[key]
            trace(ctx.app_id, "", "code", "skip_refused", {"block": key, "reason": why[:80], "note": "the text layer shows a course row in this block"})
    for key in ctx.unread():
        ctx.by_code.append(key)
        await code_read_block(ctx, key)
    ensure_planted(ctx)
    n = 0
    for f in failures(ctx):
        b = ctx.blocks[f["block"]]
        if f["row"] is None:
            if f["check"] == "text_layer_crosscheck" and f["detail"] not in b["flags"]:
                b.setdefault("flags", []).append(f["detail"])  # no cell to ask about: it is shown with the reading's flags
            continue
        if not any(q["field"] == f["field"] for q in b["rows"][f["row"]].get("questions", [])):
            attach_question(b, f["row"], f["field"], xc.question_for(f) if f["check"] != "text_layer_crosscheck" else f"Check the {f['field']} of '{f['subject']}' against the scan: {f['detail']}")
            n += 1
    trace(ctx.app_id, "", "code", "intake_summary", {"changes": len(ctx.changes), "agent_questions": ctx.questions, "code_questions": n, "recovery_used": ctx.recovery_used,
                                                    "read_by_code": ctx.by_code, "skipped": sorted(ctx.skipped)})


async def run_intake_agent(ctx: IntakeContext) -> IntakeContext:
    """The agent drives the reading; whatever it leaves is finished by code. A failure of the agent never stops the reading."""
    s = get_settings()
    pages = len({p for p, _ in ctx.spans.values()})
    brief = {"pages": pages, "blocks": len(ctx.spans), "text_layer": ctx.text is not None, "recovery_rounds": ctx.recovery_left}
    try:
        out = await run_bounded_agent(ctx.provider, IntakeRegistry([ListPages(), ViewPage(), CropRegion(), ReadBlock(), RunChecks(), FlagCell()]), ctx, system=PROMPT, user=json.dumps(brief),
                                      final_model=IntakeReport, max_tool_rounds=s.intake_max_tool_rounds, stage="intake", max_seconds=s.intake_max_seconds, submit_name=SUBMIT)
        if out.status == "OK" and out.output is not None:
            ctx.submitted = out.output.model_dump()
            for sk in out.output.skipped:
                if sk.block in ctx.spans and sk.block not in ctx.blocks:
                    ctx.skipped[sk.block] = sk.reason
            ctx.name_check = name_check(ctx.record_name, out.output.name_seen, ctx.path)
        else:
            trace(ctx.app_id, "", "intake_agent", "intake_incomplete", {"flags": out.flags}, False, out.error or "no final result")
    except Exception as e:  # noqa: BLE001  the agent is the driver, but its failure must never stop the reading: code reads what is left
        trace(ctx.app_id, "", "intake_agent", "intake_failed", {}, False, f"{type(e).__name__}: {str(e)[:120]}")
    if not ctx.name_check:
        ctx.name_check = name_check(ctx.record_name, None, ctx.path)
    await finish(ctx)
    return ctx


async def run_code_only(ctx: IntakeContext) -> IntakeContext:
    """Intake agent switched off: code reads every block (the same reader, no planning, no recovery)."""
    ctx.name_check = name_check(ctx.record_name, None, ctx.path)
    await finish(ctx)
    return ctx
