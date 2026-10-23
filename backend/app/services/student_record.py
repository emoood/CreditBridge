"""The student record (by case number) and the program plan. Code only; the demo data stands in for the student information system.

A mapped AUD equivalent is classified by where it can go in the student's plan (services/placement.py): its best slot, and every valid slot.
No valid slot means Not applicable (not in the plan, or an area blocked as a general elective)."""
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from . import placement

ROOT = Path(__file__).resolve().parents[3]

NOT_APPLICABLE = "Not applicable"


@lru_cache(maxsize=1)
def _students() -> dict[str, dict[str, Any]]:
    return yaml.safe_load((ROOT / "policies" / "demo_students.yaml").read_text())["students"]


class UnknownProgram(ValueError):
    """The student's program has no loaded plan."""


def load_record(case_number: str) -> dict[str, Any] | None:
    r = _students().get((case_number or "").strip().upper())
    if r and placement.plan(r["program"]) is None:
        raise UnknownProgram(f"No plan is loaded for the program '{r['program']}'")
    return {"case_number": case_number.strip().upper(), **r} if r else None


def case_numbers() -> list[str]:
    return list(_students())


def plan_for(program: str) -> dict[str, Any] | None:
    return placement.plan(program)


def restriction(aud_code: str | None, program: str) -> str | None:
    """Why this AUD course has no place in the student's plan (plain words), or None."""
    if not aud_code or placement.plan(program) is None or placement.options(program, aud_code):
        return None
    return placement.why_none(program, aud_code)


def classify(aud_code: str | None, program: str) -> dict[str, Any] | None:
    """The code classification of one AUD equivalent: its best slot and every valid slot; None when there is no code or no plan for the program."""
    if not aud_code or placement.plan(program) is None:
        return None
    opts = placement.options(program, aud_code)
    if not opts:
        return {"value": NOT_APPLICABLE, "why": placement.why_none(program, aud_code), "by": "code", "aud_code": aud_code, "slot": None, "options": []}
    return {"value": placement.group_of(opts[0]), "why": f"Can fill: {', '.join(placement.group_of(o) for o in opts)}", "by": "code", "aud_code": aud_code, "slot": opts[0], "options": opts}
