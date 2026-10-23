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
    "cumulative totals (credits, quality points, GPA), copy them. If the block is a courses synopsis (a course name with a description paragraph and no grade), give each course as a row with the description copied into description. Do NOT transcribe student names, ID numbers, dates of "
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
    description: str | None = None  # only for a course-synopsis row (a course name with a description paragraph)


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


def two_column_zone(img: Image.Image, min_gap: int = 30, min_zone: int = 300) -> tuple[int, int, tuple[int, int], tuple[int, int]] | None:
    """Where two tables are printed side by side: (top, bottom, left range, right range) or None. Found as the tallest run of rows in which one vertical
    gutter stays empty while there is ink on both sides of it (full-width bands above and below break the run)."""
    gray = np.asarray(img.convert("L"))
    g = gray < 200
    H, W = g.shape
    ink_rows = g.any(axis=1)
    if ink_rows.sum() < min_zone:
        return None
    cnt = g[ink_rows].sum(axis=0)
    lo, hi = int(W * 0.25), int(W * 0.75)
    free = cnt[lo:hi] <= 0.2 * ink_rows.sum()  # ink in at most a fifth of the page's ink rows: only the full-width bands above and below cross this column
    cands, run_start = [], None
    for i, f in enumerate([*free, False]):
        if f and run_start is None:
            run_start = i
        elif not f and run_start is not None:
            if i - run_start >= min_gap:
                cands.append((run_start, i))
            run_start = None
    for c0, c1 in sorted(cands, key=lambda c: c[0] - c[1]):
        gx0, gx1 = lo + c0, lo + c1
        m = max(0, (gx1 - gx0 - 12) // 2)  # only the middle of the gutter must be empty: table borders may brush its edges
        blocked = g[:, gx0 + m:gx1 - m].any(axis=1)
        y = 0
        while y < H:
            if blocked[y]:
                y += 1
                continue
            y0 = y
            while y < H and not blocked[y]:
                y += 1
            zone = g[y0:y]
            rows_ink = zone.any(axis=1).sum()
            if y - y0 >= min_zone and rows_ink >= 150 and zone[:, :gx0].any(axis=1).sum() >= 0.3 * rows_ink and zone[:, gx1:].any(axis=1).sum() >= 0.3 * rows_ink:
                cols = np.where(zone.any(axis=0))[0]
                if gx0 - int(cols[0]) < 0.3 * W or int(cols[-1]) - gx1 < 0.3 * W:
                    continue  # a gutter inside one table (between its columns), not between two tables
                faint = gray[y0:y] < 245  # two tables have a border or fill running down the inner edge of each; the ragged gap inside one plain table has none
                if faint[:, max(0, gx0 - 30):gx0].mean(axis=0).max() < 0.5 or faint[:, gx1:gx1 + 30].mean(axis=0).max() < 0.5:
                    continue
                return y0, y, (max(0, int(cols[0]) - 8), gx0 + 4), (gx1 - 4, min(W, int(cols[-1]) + 9))
    return None


def layout(img: Image.Image, max_h: int) -> list[tuple[tuple[int, int], tuple[int, int] | None, tuple[int, int]]]:
    """The blocks of one page as (vertical span, horizontal range or None, (group, column)): text above a two-column zone, the left column of the zone
    top to bottom, its right column, then the text below. A page with no side-by-side tables is the plain top-to-bottom split."""
    z = two_column_zone(img)
    if z is None:
        return [(sp, None, (1, 0)) for sp in split_blocks(img, max_h)]
    top, bottom, left, right = z
    out: list[tuple[tuple[int, int], tuple[int, int] | None, tuple[int, int]]] = []
    for sp in split_blocks(img.crop((0, 0, img.width, top)), max_h):
        out.append((sp, None, (1, 0)))
    for ci, xr in enumerate((left, right)):
        for a, b in split_blocks(img.crop((xr[0], top, xr[1], bottom)), max_h):
            out.append(((top + a, top + b), xr, (2, ci)))
    for a, b in split_blocks(img.crop((0, bottom, img.width, img.height)), max_h):
        out.append(((bottom + a, bottom + b), None, (3, 0)))
    return out


def clean_background(img: Image.Image, level: int = 215) -> Image.Image:
    """Whiten the pale background (a diagonal watermark, light table fills, scanner tint) so the print stands out; dark print and dark header fills are untouched."""
    a = np.asarray(img.convert("RGB")).copy()
    a[(a > level).all(axis=2)] = 255
    return Image.fromarray(a)


def crop_png_b64(img: Image.Image, span: tuple[int, int], scale: float = 1.0, xr: tuple[int, int] | None = None) -> str:
    a, b = int(span[0] * scale), int(span[1] * scale)
    x0, x1 = (int(xr[0] * scale), int(xr[1] * scale)) if xr else (0, img.width)
    buf = io.BytesIO()
    clean_background(img.crop((x0, a, min(img.width, x1), min(img.height, b)))).save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


async def read_block(provider: LLMProvider, image_b64: str, unit: str, hint: str | None = None) -> BlockReading:
    ask = "Read this transcript block." + (f" A check failed on a previous reading: {hint} Look closely at that cell and copy exactly what is printed." if hint else "")
    req = LLMRequest([{"role": "system", "content": PROMPT}, {"role": "user", "content": ask, "images": [image_b64]}],
                     [_tool()], "read", {"course": unit, "redact": True})
    resp = await provider.complete(req)
    call = next((c for c in resp.tool_calls if c.name == SUBMIT), None)
    try:
        if call:
            return BlockReading.model_validate(normalize_reading(call.arguments))
        text = resp.content
        a = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
        if a < 0:
            raise ValueError("no JSON in the reply")
        obj = json.loads(text[a:text.rindex("]" if text[a] == "[" else "}") + 1])  # some runtimes put the JSON in content
        return BlockReading.model_validate(normalize_reading(obj))
    except (ValueError, ValidationError) as e:
        raise VisionError("invalid_output") from e


def normalize_reading(obj: Any) -> Any:
    """Tolerate the shapes a small model really produces, without inventing anything: a list of term objects (their rows are joined; totals are kept only when exactly one
    term has them), and a non-numeric "mark" such as "A" (that is a letter grade, so it moves to the letter column)."""
    if isinstance(obj, list):
        terms = [t for t in obj if isinstance(t, dict)]
        with_tot = [t for t in terms if any(t.get(k) is not None for k in ("term_credits", "term_quality_points", "term_gpa"))]
        merged: dict[str, Any] = {"rows": [r for t in terms for r in (t.get("rows") or [])]}
        if len(with_tot) == 1:
            merged.update({k: v for k, v in with_tot[0].items() if k != "rows"})
        obj = merged
    if isinstance(obj, dict):
        rows = []
        for r in obj.get("rows") or []:
            if isinstance(r, dict) and isinstance(r.get("mark"), str):
                try:
                    r = {**r, "mark": float(r["mark"])}
                except ValueError:
                    r = {**r, "mark": None, "letter": r.get("letter") or r["mark"]}
            rows.append(r)
        obj = {**obj, "rows": rows}
    return obj


def plant_misread(reading: BlockReading, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """PLANTED FOR DEMO: replace one correctly read value (a mark, a letter or credits, as configured) with a wrong one BEFORE the extraction checks run.
    Returns what was changed, or None."""
    f = cfg.get("field", "mark")
    for r in reading.rows:
        if cfg["subject"].lower() in (r.subject or "").lower() and getattr(r, f, None) is not None:
            wrong = str(cfg["wrong"]) if f == "letter" else float(cfg["wrong"])
            rec = {"subject": r.subject, "field": f, "from": getattr(r, f), "to": wrong}
            setattr(r, f, wrong)
            return rec
    return None


def move_credits_from_points(d: dict[str, Any], scale: dict[str, Any]) -> None:
    """A small model often files a transcript's credits column under grade points when the page has no grade-points column. The tell is exact: credits are empty, the
    "points" number is not what the printed letter is worth, and it is a plausible credit value. Then it is credits (the row keeps a note saying so). Anything else is left
    for the checks to flag."""
    pts, letter = d.get("points"), (d.get("letter") or "").strip()
    if d.get("credits") is None and pts is not None and letter and d.get("mark") is None and 0 < pts <= 6:
        exp = gc.points_for_letter(letter, scale)
        if exp is not None and abs(pts - exp) > gc.TOL_POINTS:
            d["credits"], d["points"], d["normalized"] = pts, None, ["points column read as credits"]
    elif d.get("credits") is not None and pts is not None and letter and d.get("mark") is None and abs(pts - d["credits"]) < 1e-9:  # the same number twice: the credits copied into points
        exp = gc.points_for_letter(letter, scale)
        if exp is not None and abs(pts - exp) > gc.TOL_POINTS:
            d["points"], d["normalized"] = None, ["points copied from the credits column"]


def check_block(b: BlockReading, scale: dict[str, Any]) -> dict[str, Any]:
    rows, flags = [], []
    for r in b.rows:
        d = r.model_dump()
        move_credits_from_points(d, scale)
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
