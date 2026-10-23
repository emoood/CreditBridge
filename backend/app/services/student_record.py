"""The student record (by case number) and the program plan. Code only; the demo data stands in for the student information system.

A mapped AUD equivalent is classified against the student's program plan:
  Program course               required or an allowed program elective
  General elective suggestion  on the plan's general-elective list
  Not applicable               in neither list (the Case agent may judge a borderline one against the free-elective allowance)"""
from functools import lru_cache
from typing import Any

import yaml

from .mapping import ROOT

PROGRAM, ELECTIVE, NOT_APPLICABLE = "Program course", "General elective suggestion", "Not applicable"
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


def case_numbers() -> list[str]:
    return list(_students())


def plan_for(program: str) -> dict[str, Any] | None:
    return _plans().get((program or "").strip())


def classify(aud_code: str | None, program: str) -> dict[str, Any] | None:
    """The code classification of one AUD equivalent, or None when there is no code or no plan for the program."""
    plan = plan_for(program)
    if not aud_code or plan is None:
        return None
    if aud_code in plan["required"]:
        return {"value": PROGRAM, "why": f"Required in {program}", "by": "code", "aud_code": aud_code}
    if aud_code in plan["allowed_electives"]:
        return {"value": PROGRAM, "why": f"Program elective in {program}", "by": "code", "aud_code": aud_code}
    if aud_code in plan["general_electives"]:
        return {"value": ELECTIVE, "why": f"On the general-elective list of {program}", "by": "code", "aud_code": aud_code}
    return {"value": NOT_APPLICABLE, "why": f"{aud_code} is not on the plan for {program}", "by": "code", "aud_code": aud_code}


def allowance_left(program: str, used_credits: int) -> int | None:
    plan = plan_for(program)
    return None if plan is None else int(plan.get("free_elective_credits", 0)) - used_credits
