"""Deterministic cross-checks on what the vision model read: mark -> letter -> grade points, credit totals, quality points,
GPA. A mismatch only raises a flag for the officer; it never changes a value. The scale is an assumption (see the YAML)."""
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

SCALES_PATH = Path(__file__).resolve().parents[3] / "policies" / "school_grade_scales.yaml"
TOL_POINTS = 0.051  # printed points are rounded to 1 decimal
TOL_TOTAL = 0.02


@lru_cache
def load_scale(name: str | None = None) -> dict[str, Any]:
    d = yaml.safe_load(SCALES_PATH.read_text())
    return d["scales"][name or d["default"]]


BAND = re.compile(r"\b([A-F][+-]?)\s*[:\s]\s*(\d{2,3})\s*(?:-|–|to)\s*(\d{2,3})\b(?:[^|;\n]*?=\s*(\d(?:\.\d)?))?")


def scale_from_text(text: str) -> dict[str, Any] | None:
    """Read a printed grading scale such as 'A 93-100 = 4.0 | A- 90-92 = 3.7 | ...' into bands (min_mark, letter, points). None if fewer than 3 bands are found."""
    found = {}
    for m in BAND.finditer(text.replace("\u2013", "-")):
        letter, lo, _hi, pts = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        found[letter] = (lo, letter, float(pts) if pts else None)
    if len(found) < 3:
        return None
    bands = sorted(found.values(), key=lambda b: -b[0])
    default = {b[1]: b[2] for b in load_scale()["bands"]}
    return {"label": "Grading scale printed on the transcript", "source": "printed", "bands": [[lo, lt, pts if pts is not None else default.get(lt, 0.0)] for lo, lt, pts in bands]}


def letter_for_row(row: dict[str, Any], scale: dict[str, Any] | None = None) -> tuple[str | None, str]:
    """The letter grade a rule should use: the letter column when present; otherwise the numeric mark converted with the scale printed on the
    transcript (falling back to the default synthetic scale, which is only an assumption). Returns (letter or None, how it was found)."""
    g = (row.get("grade") or "").strip()
    if g and not re.fullmatch(r"\d+(\.\d+)?", g):
        return g.upper(), "letter column"
    mark = row.get("mark")
    if mark is None and re.fullmatch(r"\d+(\.\d+)?", g):
        mark = float(g)
    if mark is None:
        return None, "no grade"
    s = scale or load_scale()
    return expected_for_mark(float(mark), s)[0], "numeric mark converted with the " + ("printed" if scale and scale.get("source") in ("printed", "configured") else "default (assumed)") + " grading scale"


def expected_for_mark(mark: float, scale: dict[str, Any]) -> tuple[str, float]:
    for lo, letter, pts in scale["bands"]:
        if mark >= lo:
            return letter, pts
    return scale["bands"][-1][1], scale["bands"][-1][2]


def points_for_letter(letter: str, scale: dict[str, Any]) -> float | None:
    return next((pts for _, lt, pts in scale["bands"] if lt.upper() == letter.strip().upper()), None)


@dataclass
class RowCheck:
    flags: list[str] = field(default_factory=list)
    uncertain: list[str] = field(default_factory=list)


def check_row(row: dict[str, Any], scale: dict[str, Any]) -> RowCheck:
    c = RowCheck()
    mark, letter, pts = row.get("mark"), row.get("letter"), row.get("points")
    if not row.get("subject"):
        c.flags.append("subject_unreadable"); c.uncertain.append("title")
    if mark is not None:
        exp_letter, exp_pts = expected_for_mark(mark, scale)
        if letter and letter.strip().upper() != exp_letter.upper():
            c.flags.append(f"letter_mismatch(mark {mark:g} -> {exp_letter}, printed {letter})"); c.uncertain.append("grade")
        if pts is not None and abs(pts - exp_pts) > TOL_POINTS:
            c.flags.append(f"points_mismatch(mark {mark:g} -> {exp_pts}, printed {pts:g})"); c.uncertain.append("points")
    elif letter and pts is not None:
        exp = points_for_letter(letter, scale)
        if exp is not None and abs(pts - exp) > TOL_POINTS:
            c.flags.append(f"points_mismatch(letter {letter} -> {exp}, printed {pts:g})"); c.uncertain.append("points")
    if not letter and mark is None:
        c.flags.append("no_grade_read"); c.uncertain.append("grade")
    if row.get("credits") is None:
        c.uncertain.append("credits")
    return c


def check_totals(rows: list[dict[str, Any]], credits: float | None, quality: float | None, gpa: float | None, label: str) -> list[str]:
    """Recompute credits, quality points and GPA from the rows and compare with the printed totals."""
    flags: list[str] = []
    usable = [r for r in rows if r.get("credits") is not None]
    if not usable or len(usable) != len(rows):
        return flags  # cannot recompute from incomplete rows; the missing credits are already flagged per row
    cr = sum(r["credits"] for r in usable)
    if credits is not None and abs(cr - credits) > TOL_TOTAL:
        flags.append(f"{label}_credits_mismatch(computed {cr:g}, printed {credits:g})")
    pts_rows = [r for r in usable if r.get("points") is not None]
    if len(pts_rows) == len(usable):
        qp = sum(r["points"] * r["credits"] for r in usable)
        if quality is not None and abs(qp - quality) > 0.06:
            flags.append(f"{label}_points_mismatch(computed {qp:.2f}, printed {quality:g})")
        if gpa is not None and cr and abs(qp / cr - gpa) > 0.015:
            flags.append(f"{label}_gpa_mismatch(computed {qp / cr:.2f}, printed {gpa:g})")
    return flags
