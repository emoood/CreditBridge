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


def question_for(f: dict[str, Any]) -> str:
    if f["check"] in TOTAL_FIELD:
        return (f"The printed term {f['label']} total is {f['printed']} but the rows add up to {f['computed']}. "
                f"Check the {f['field']} of '{f['subject'] or 'this row'}' against the scan, or whether a row is missing.")
    return f"Check the {f['field']} of '{f['subject'] or 'this row'}' against the scan: {f['detail']}"
