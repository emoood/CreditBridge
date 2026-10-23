"""The extraction checks as tools: run on ONE block (a dict with rows, flags and totals as stored per read unit).
Each failure names the exact cell, what the check expected and what was read. Code only, no model."""
import re
from typing import Any

from . import grade_checks as gc

FIELD_OF = {"letter_mismatch": "grade", "points_mismatch": "points", "no_grade_read": "grade", "subject_unreadable": "title"}


def is_non_course(r: dict[str, Any]) -> bool:
    """Headings, legends and notes a block read picked up: no grade and no credits. They are not checked (the merge drops them too)."""
    return r.get("mark") is None and (r.get("letter") or "").strip().lower() in ("", "null", "none") and r.get("credits") is None


def prior_rows(blocks: dict[str, dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """Course rows of the same term printed in EARLIER blocks (since the last printed term total): a term's table can span blocks, so a block's
    total line has to be compared with all of its term's rows, not only the rows in its own block."""
    acc: list[dict[str, Any]] = []
    for k, b in blocks.items():
        if k == key:
            return acc
        acc += [r for r in b.get("rows", []) if not is_non_course(r)]
        if any(b.get("totals", {}).get(x) is not None for x in ("term_credits", "term_quality_points", "term_gpa")):
            acc = []
    return acc


def failures_in(blocks: dict[str, dict[str, Any]], key: str, scale: dict[str, Any]) -> list[dict[str, Any]]:
    return block_failures(blocks[key], scale, prior_rows(blocks, key))


def block_failures(block: dict[str, Any], scale: dict[str, Any], prior: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Grading-scale checks per row, and per-term credit, grade-point and GPA totals when the block shows them (compared with the term's rows
    in earlier blocks too, via `prior`)."""
    if block.get("failed"):  # the model returned no usable reading for this block at all
        return [{"check": "block_unreadable", "row": None, "subject": None, "field": "block", "detail": f"the block could not be read ({block.get('error') or 'invalid output'})"}]
    out: list[dict[str, Any]] = []
    rows = block.get("rows", [])
    for i, r in enumerate(rows):
        if is_non_course(r):
            continue
        for f in gc.check_row(r, scale).flags:
            kind = f.split("(")[0]
            out.append({"check": "grading_scale", "row": i, "subject": r.get("subject"), "field": FIELD_OF.get(kind, "grade"), "detail": f})
    t = block.get("totals", {})
    course_rows = [*(prior or []), *[r for r in rows if not is_non_course(r)]]
    if any(t.get(k) is not None for k in ("term_credits", "term_quality_points", "term_gpa")):
        for f in gc.check_totals(course_rows, t.get("term_credits"), t.get("term_quality_points"), t.get("term_gpa"), "term"):
            kind = f.split("_mismatch")[0]
            out.append({"check": {"term_credits": "credit_total", "term_points": "grade_point_total", "term_gpa": "gpa_total"}.get(kind, kind), "row": None,
                        "subject": None, "field": "term total", "detail": f})
    return out


TOTAL_FIELD = {"credit_total": ("credits", "credit"), "grade_point_total": ("points", "grade-point"), "gpa_total": ("points", "GPA")}
NUMS = re.compile(r"computed ([\d.]+), printed ([\d.]+)")


def term_cells(blocks: dict[str, dict[str, Any]], key: str) -> list[tuple[str, int]]:
    """(block key, row index) of every course row in the term whose total line is in block `key` (rows of earlier blocks included)."""
    acc: list[tuple[str, int]] = []
    for k, b in blocks.items():
        acc += [(k, i) for i, r in enumerate(b.get("rows", [])) if not is_non_course(r)]
        if k == key:
            return acc
        if any(b.get("totals", {}).get(x) is not None for x in ("term_credits", "term_quality_points", "term_gpa")):
            acc = []
    return acc


def cell_failures(blocks: dict[str, dict[str, Any]], scale: dict[str, Any]) -> list[dict[str, Any]]:
    """Every failing check as exact cells. A failed term total is attached to each row of that term (the printed and the computed number are in
    the entry), because a total cannot say which row is wrong. `block` is where the cell is; `via` is the block whose check failed."""
    out: list[dict[str, Any]] = []
    for key in blocks:
        for f in failures_in(blocks, key, scale):
            if f["row"] is not None or f["check"] == "block_unreadable":
                out.append({**f, "block": key, "via": key})
            elif f["check"] in TOTAL_FIELD:
                fld, label = TOTAL_FIELD[f["check"]]
                m = NUMS.search(f["detail"])
                for bk, ri in term_cells(blocks, key):
                    out.append({"check": f["check"], "block": bk, "via": key, "row": ri, "subject": blocks[bk]["rows"][ri].get("subject"), "field": fld,
                                "detail": f["detail"], "label": label, "computed": m.group(1) if m else None, "printed": m.group(2) if m else None})
    return out


# ----------------------------------------------------------------------------------------------- the extraction header (chunk C1, step 2)
# One field, "Institution / curriculum": a curriculum for a school transcript, a university name for a university transcript. Student type follows from it.
# The bare tokens AP and IB match upper case only (so the word "ap" never counts); the spelled-out names match in any case.
CURRICULA = [("AP", r"(?i:advanced placement)|\bAP\b"), ("IB", r"(?i:international baccalaureate)|\bIB\b"), ("A Level", r"(?i:\bgce\b|\ba[- ]?levels?\b|\bas[- ]levels?\b)"),
             ("French Bac", r"(?i:baccalaur[eé]at\b|\bfrench bac\b|\bbac\b)")]
UNIVERSITY = re.compile(r"(?i)\b(universit(?:y|é|ät|à)|college|institute of technology|polytechnic|école|hochschule)\b")
MAJOR_LINE = re.compile(r"(?im)^\s*(?:major|field of study|degree program(?:me)?|program(?:me)? of study)\s*[:\-]\s*(\S.*?)\s*$")
TABLE_HEAD = re.compile(r"(?i)^\s*(subject|course|module|title)\b.*\b(grade|credits?|mark|result)\b")


def curriculum_of(text: str | None) -> str | None:
    """The canonical curriculum a text names (AP, IB, A Level, French Bac), or None. 'Advanced Placement', 'AP' and 'A-Level' all normalise."""
    t = (text or "").strip()
    return next((name for name, pattern in CURRICULA if re.search(pattern, t)), None)


def student_type_of(value: str | None) -> str | None:
    """'school' when the value is a curriculum, 'university' when it names a university; None when it is neither (the officer decides)."""
    if not (value or "").strip():
        return None
    if curriculum_of(value):
        return "school"
    return "university" if UNIVERSITY.search(value or "") else None


def normalise_header_value(value: str | None) -> str | None:
    """A curriculum is stored under its canonical name; a university keeps the name as printed; blank is None."""
    v = re.sub(r"\s+", " ", (value or "").strip(" .:-"))
    if not v or v.lower() in ("null", "none", "n/a", "unknown"):
        return None
    return curriculum_of(v) or v[:120]


def header_lines(text: str, limit: int = 14) -> list[str]:
    """The lines printed above the first table (at most `limit`): where an institution, a curriculum or a major would be printed."""
    out: list[str] = []
    for ln in text.splitlines():
        if TABLE_HEAD.match(ln) or len(out) >= limit:
            break
        if ln.strip():
            out.append(ln.strip())
    return out


def header_from_text(path: str, titles: list[str]) -> dict[str, Any]:
    """What the text layer of a digital PDF says about the header: {'value', 'basis', 'major'} (each may be None). Only a cross-check, or the fallback when the agent
    read nothing; image-only files have no text layer and give None everywhere. A curriculum is looked for in the header lines, then inferred from the course names
    (most of them start with 'AP ' or carry HL / SL)."""
    out: dict[str, Any] = {"value": None, "basis": None, "major": None}
    if not path.lower().endswith(".pdf"):
        return out
    try:
        import pymupdf
        text = "\n".join(p.get_text() for p in pymupdf.open(path))
    except Exception:  # noqa: BLE001  an unreadable file just has no text layer
        return out
    if len(text) < 80:
        return out
    head = "\n".join(header_lines(text))
    if (c := curriculum_of(head)):
        return {**out, "value": c, "basis": "printed in the header"}
    uni = next((ln for ln in head.splitlines() if UNIVERSITY.search(ln) and len(ln) < 90), None)
    if uni:
        m = MAJOR_LINE.search(head)
        return {**out, "value": uni.title() if uni.isupper() else uni, "basis": "printed in the header", "major": m.group(1).strip()[:120] if m else None}
    named = [t for t in titles if t]
    if named and sum(1 for t in named if re.match(r"AP\s", t.strip())) >= max(2, len(named) // 2):
        return {**out, "value": "AP", "basis": "inferred: most course names start with 'AP'"}
    if named and sum(1 for t in named if re.search(r"\b(HL|SL)\b", t)) >= max(2, len(named) // 2):
        return {**out, "value": "IB", "basis": "inferred: most course names carry HL or SL"}
    return out


def curriculum_check(record_curriculum: str | None, value: str | None) -> dict[str, Any]:
    """The curriculum on the transcript against the one on the case record. A university transcript is not compared (the record's curriculum is a school one)."""
    rec = curriculum_of(record_curriculum)
    seen = curriculum_of(value)
    if not rec or not value:
        return {"status": "unchecked", "record": rec, "seen": seen or value}
    if seen is None:
        return {"status": "unchecked", "record": rec, "seen": value}
    return {"status": "match" if seen == rec else "mismatch", "record": rec, "seen": seen}


def credits_view(student_type: str | None, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Credits per the requirements: a school transcript shows them only if printed (and needs none otherwise); a university transcript requires them, so an included row
    without credits is 'credits missing'. {'show', 'required', 'missing': [row ids]}."""
    printed = any(str(r.get("credits") or "").strip() for r in rows)
    required = student_type == "university"
    return {"show": required or printed, "required": required,
            "missing": [r["id"] for r in rows if required and r.get("included", True) and not str(r.get("credits") or "").strip()]}


def question_for(f: dict[str, Any]) -> str:
    if f["check"] in TOTAL_FIELD:
        return (f"The printed term {f['label']} total is {f['printed']} but the rows add up to {f['computed']}. "
                f"Check the {f['field']} of '{f['subject'] or 'this row'}' against the scan, or whether a row is missing.")
    return f"Check the {f['field']} of '{f['subject'] or 'this row'}' against the scan: {f['detail']}"
