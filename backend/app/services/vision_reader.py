"""Vision reader: render pages, split them into blocks at whitespace, have the vision model read ONE block at a time into
a schema-constrained structure (copy what is printed, null if unreadable), then cross-check in code. The model never sees
or returns personal details by instruction, and block content is never logged."""
import base64
import io
import json
from typing import Any

import numpy as np
import pymupdf
from PIL import Image
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

from ..providers.base import LLMProvider, LLMRequest
from . import grade_checks as gc

SUBMIT = "submit_block"
PROMPT = (
    "You read ONE block of a school transcript image. Copy EXACTLY what is printed; never guess, round or infer. "
    "Include EVERY subject row in the block, even unusual or non-standard subjects; never skip a row. Use null for anything unreadable or absent. Subject names are copied as printed. Marks are numbers; letters are the "
    "printed letter grade; points are the printed grade points; credits are printed credits. If the block shows term or "
    "cumulative totals (credits, quality points, GPA), copy them. Do NOT transcribe student names, ID numbers, dates of "
    "birth or any other personal details. Call submit_block exactly once.")


class VisionRow(BaseModel):
    model_config = ConfigDict(extra="ignore")
    subject: str | None = None
    code: str | None = None
    mark: float | None = None
    letter: str | None = Field(default=None, validation_alias=AliasChoices("letter", "grade"))
    points: float | None = None
    credits: float | None = None
    term: str | None = None


class BlockReading(BaseModel):
    model_config = ConfigDict(extra="ignore")
    rows: list[VisionRow] = Field(default_factory=list)
    term: str | None = None
    term_credits: float | None = None
    term_quality_points: float | None = None
    term_gpa: float | None = None
    cumulative_credits: float | None = None
    cumulative_quality_points: float | None = None
    cumulative_gpa: float | None = None


class VisionError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _tool() -> dict[str, Any]:
    from ..agents.json_action import inline_refs
    return {"type": "function", "function": {"name": SUBMIT, "description": "Submit the rows and totals you read from this block.",
                                             "parameters": inline_refs(BlockReading.model_json_schema())}}


def render_pages(path: str, dpi: int) -> list[Image.Image]:
    p = path.lower()
    if p.endswith(".pdf"):
        return [Image.open(io.BytesIO(pg.get_pixmap(dpi=dpi).tobytes("png"))).convert("RGB") for pg in pymupdf.open(path)]
    return [Image.open(path).convert("RGB")]


def split_blocks(img: Image.Image, max_h: int) -> list[tuple[int, int]]:
    """Vertical (top, bottom) spans of content, each at most max_h tall, cut where the page is emptiest (never mid-row if
    avoidable). Blank pages give no blocks."""
    ink = (np.asarray(img.convert("L")) < 140).sum(axis=1).astype(float)
    ink = np.convolve(ink, np.ones(5) / 5, mode="same")
    rows = np.where(ink > 2)[0]
    if rows.size == 0:
        return []
    top, bottom = max(0, int(rows[0]) - 12), min(len(ink), int(rows[-1]) + 12)
    spans, start = [], top
    while bottom - start > max_h:
        lo, hi = start + int(max_h * 0.55), start + max_h
        cut = lo + int(np.argmin(ink[lo:hi]))
        spans.append((start, cut))
        start = cut
    spans.append((start, bottom))
    return [(a, b) for a, b in spans if (ink[a:b] > 2).sum() > 8]


def count_text_lines(img: Image.Image, span: tuple[int, int]) -> int:
    """About how many text lines are printed in this vertical span (runs of ink rows), so a reading that returned far fewer course rows can be questioned."""
    ink = (np.asarray(img.convert("L"))[span[0]:span[1]] < 140).sum(axis=1)
    on = ink > 2
    return int(((on[1:] & ~on[:-1]).sum()) + (1 if on.size and on[0] else 0))


def crop_png_b64(img: Image.Image, span: tuple[int, int], scale: float = 1.0) -> str:
    a, b = int(span[0] * scale), int(span[1] * scale)
    buf = io.BytesIO()
    img.crop((0, a, img.width, min(img.height, b))).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


async def read_block(provider: LLMProvider, image_b64: str, unit: str, hint: str | None = None) -> BlockReading:
    ask = "Read this transcript block." + (f" A check failed on a previous reading: {hint} Look closely at that cell and copy exactly what is printed." if hint else "")
    req = LLMRequest([{"role": "system", "content": PROMPT}, {"role": "user", "content": ask, "images": [image_b64]}],
                     [_tool()], "read", {"course": unit, "redact": True})
    resp = await provider.complete(req)
    call = next((c for c in resp.tool_calls if c.name == SUBMIT), None)
    try:
        if call:
            return BlockReading.model_validate(call.arguments)
        s, e = resp.content.index("{"), resp.content.rindex("}") + 1  # some runtimes put the JSON in content
        return BlockReading.model_validate(json.loads(resp.content[s:e]))
    except (ValueError, ValidationError) as e:
        raise VisionError("invalid_output") from e


def plant_misread(reading: BlockReading, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """PLANTED FOR DEMO: replace one correctly read mark with a wrong one BEFORE the extraction checks run. Returns what was changed, or None."""
    for r in reading.rows:
        if cfg["subject"].lower() in (r.subject or "").lower() and r.mark is not None:
            rec = {"subject": r.subject, "field": "mark", "from": r.mark, "to": float(cfg["wrong"])}
            r.mark = float(cfg["wrong"])
            return rec
    return None


def check_block(b: BlockReading, scale: dict[str, Any]) -> dict[str, Any]:
    rows, flags = [], []
    for r in b.rows:
        d = r.model_dump()
        c = gc.check_row(d, scale)
        d["flags"], d["uncertain"] = c.flags, c.uncertain
        rows.append(d)
        flags += [f"{(d.get('subject') or '?')[:30]}: {f}" for f in c.flags]
    return {"rows": rows, "flags": flags, "totals": b.model_dump(exclude={"rows"})}


def needs_reread(checked: dict[str, Any]) -> bool:
    """A block is re-read once at higher resolution when its content or its cross-checks look wrong."""
    return bool(checked["flags"])


def is_non_course(r: dict[str, Any]) -> bool:
    """A course row has a grade (mark or letter) or credits. Anything else (headings, grading-scale legends, notes) is not a course."""
    return r.get("mark") is None and (r.get("letter") or "").strip().lower() in ("", "null", "none") and r.get("credits") is None  # the model sometimes writes the word "null"


def drop_non_courses(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return [r for r in rows if not is_non_course(r)], [r for r in rows if is_non_course(r)]


def merge_checked(blocks: list[dict[str, Any]], scale: dict[str, Any]) -> dict[str, Any]:
    """Merge per-block results in order; run the cumulative cross-check across all rows."""
    rows = [r for b in blocks for r in b["rows"]]
    rows, dropped = drop_non_courses(rows)
    cum: dict[str, float] = {}
    term_flags: list[str] = []
    acc: list[dict[str, Any]] = []
    for b in blocks:  # a term's table can span blocks: compare each printed term total with all rows read since the previous total
        acc += [r for r in b["rows"] if not is_non_course(r)]
        t = b["totals"]
        if any(t.get(k) is not None for k in ("term_credits", "term_quality_points", "term_gpa")):
            term_flags += gc.check_totals(acc, t.get("term_credits"), t.get("term_quality_points"), t.get("term_gpa"), "term")
            acc = []
        for k in ("cumulative_credits", "cumulative_quality_points", "cumulative_gpa"):
            if b["totals"].get(k) is not None:
                cum[k] = b["totals"][k]
    dropped_prefixes = tuple(f"{(d.get('subject') or '?')[:30]}:" for d in dropped)
    flags = [f for b in blocks for f in b["flags"] if not f.startswith(dropped_prefixes)]  # drop flags that belonged to non-course rows
    flags += term_flags
    flags += gc.check_totals(rows, cum.get("cumulative_credits"), cum.get("cumulative_quality_points"), cum.get("cumulative_gpa"), "cumulative")
    legend = " | ".join(" ".join(str(d.get(k)) for k in ("subject", "letter", "mark", "points") if d.get(k) is not None) for d in dropped)  # a printed grading scale ends up here
    return {"rows": rows, "flags": flags, "cumulative": cum, "scale": scale["label"], "dropped_rows": len(dropped), "dropped_text": legend}
