"""The student record (by case number) and the program plan. Code only; the demo data stands in for the student information system.

A mapped AUD equivalent is classified by policies/program_constraints.yaml, in order:
  Not applicable               in a blocked area or a blocked course of the program
  Core course                  one of the program's core courses
  General elective suggestion  any other mapped course (no list, no cap)"""
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]

PROGRAM, ELECTIVE, NOT_APPLICABLE = "Core course", "General elective suggestion", "Not applicable"
CLASSES = (PROGRAM, ELECTIVE, NOT_APPLICABLE)


@lru_cache(maxsize=1)
def _students() -> dict[str, dict[str, Any]]:
    return yaml.safe_load((ROOT / "policies" / "demo_students.yaml").read_text())["students"]


@lru_cache(maxsize=1)
def _plans() -> dict[str, dict[str, Any]]:
    return yaml.safe_load((ROOT / "policies" / "program_plans.yaml").read_text())["programs"]


def load_record(case_number: str) -> dict[str, Any] | None:
    r = _students().get((case_number or "").strip().upper())
    return {"case_number": case_number.strip().upper(), **r} if r else None


@lru_cache(maxsize=1)
def _constraints() -> dict[str, dict[str, Any]]:
    return yaml.safe_load((ROOT / "policies" / "program_constraints.yaml").read_text())["programs"]


def constraints_for(program: str) -> dict[str, Any] | None:
    p = (program or "").strip().lower()
    return next((c for c in _constraints().values() if p in [n.lower() for n in c.get("names", [])]), None)


def case_numbers() -> list[str]:
    return list(_students())


def plan_for(program: str) -> dict[str, Any] | None:
    return _plans().get((program or "").strip())


def restriction(aud_code: str | None, program: str) -> str | None:
    """Why this AUD course is not open to the student's program (blocked areas and courses), or None. Plain words, for the officer."""
    c = constraints_for(program)
    if not aud_code or c is None:
        return None
    if aud_code.split()[0] in c.get("blocked_areas", []) or aud_code in c.get("blocked_courses", []):
        return (c.get("reasons") or {}).get("blocked") or f"{aud_code} is not open to {program} programs."
    return None


def classify(aud_code: str | None, program: str) -> dict[str, Any] | None:
    """The code classification of one AUD equivalent, or None when there is no code or no constraints for the program."""
    c = constraints_for(program)
    if not aud_code or c is None:
        return None
    why_not = restriction(aud_code, program)
    if why_not:
        return {"value": NOT_APPLICABLE, "why": why_not, "by": "code", "aud_code": aud_code}
    if aud_code in c.get("core_courses", []):
        return {"value": PROGRAM, "why": f"Core course in {program}", "by": "code", "aud_code": aud_code}
    return {"value": ELECTIVE, "why": "Potential general elective", "by": "code", "aud_code": aud_code}
