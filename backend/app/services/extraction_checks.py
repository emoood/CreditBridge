"""The extraction checks as tools: run on ONE block (a dict with rows, flags and totals as stored per read unit).
Each failure names the exact cell, what the check expected and what was read. Code only, no model."""
from typing import Any

from . import grade_checks as gc

FIELD_OF = {"letter_mismatch": "grade", "points_mismatch": "points", "no_grade_read": "grade", "subject_unreadable": "title"}


def block_failures(block: dict[str, Any], scale: dict[str, Any]) -> list[dict[str, Any]]:
    """Grading-scale checks per row, and per-term credit, grade-point and GPA totals when the block shows them."""
    out: list[dict[str, Any]] = []
    rows = block.get("rows", [])
    for i, r in enumerate(rows):
        for f in gc.check_row(r, scale).flags:
            kind = f.split("(")[0]
            out.append({"check": "grading_scale", "row": i, "subject": r.get("subject"), "field": FIELD_OF.get(kind, "grade"), "detail": f})
    t = block.get("totals", {})
    course_rows = [r for r in rows if not (r.get("mark") is None and not r.get("letter") and r.get("credits") is None)]
    if any(t.get(k) is not None for k in ("term_credits", "term_quality_points", "term_gpa")):
        for f in gc.check_totals(course_rows, t.get("term_credits"), t.get("term_quality_points"), t.get("term_gpa"), "term"):
            kind = f.split("_mismatch")[0]
            out.append({"check": {"term_credits": "credit_total", "term_points": "grade_point_total", "term_gpa": "gpa_total"}.get(kind, kind), "row": None,
                        "subject": None, "field": "term total", "detail": f})
    return out
