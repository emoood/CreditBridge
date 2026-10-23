"""Code checks behind the Case agent's whole-application review. The agent raises a doubt or judges a borderline elective; code decides whether the
facts support it. A doubt of a named kind that code cannot corroborate is refused, so the agent cannot invent a problem. No model here."""
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import CourseMapping
from . import actions as ac
from . import mapping as mp
from . import policy_rules as pr
from . import student_record as sr

KINDS = ("stale_mapping", "near_miss_grade", "program_mismatch", "other")


def newest_version(db: Session) -> str:
    return max((m.version for m in db.scalars(select(CourseMapping))), default="")


def signals(db: Session, repo, app, row: dict[str, Any], st) -> dict[str, Any]:
    """What code can see for one row: the facts the three structured doubt kinds are checked against."""
    m = st.mapping or {}
    code = m.get("aud_code")
    out: dict[str, Any] = {"stale_mapping": None, "near_miss_grade": None, "program_mismatch": None}
    if not m:
        return out
    live = db.get(CourseMapping, m["id"]) if m.get("id") else None
    newest = newest_version(db)
    if (live is not None and live.status != "ACTIVE") or (m.get("version") and newest and m["version"] < newest):
        out["stale_mapping"] = f"mapping version {m.get('version')} is older than the newest in the table ({newest})" if m.get("version") != newest else f"mapping is {live.status.lower()}"
    cond, facts = m.get("condition") or {}, st.facts or {}
    p = pr.Policy.load()
    grade = (row.get("grade") or "").strip().upper()
    if cond.get("type") == "min_exam_score" and facts.get("exam_score") is not None and float(facts["exam_score"]) == float(cond["value"]):
        out["near_miss_grade"] = f"exam score {facts['exam_score']} is exactly the minimum {cond['value']}"
    else:
        level = (repo.targets.get(code) or {}).get("level", 200)
        minimum = cond.get("value") if cond.get("type") == "min_grade" else (p.min_grade_lower if level <= 200 else p.min_grade_upper)
        if grade in p.grade_scale and minimum in p.grade_scale:
            gap = p.grade_scale.index(minimum) - p.grade_scale.index(grade)
            if gap in (0, 1):
                out["near_miss_grade"] = f"grade {grade} is {'exactly at' if gap == 0 else 'one step above'} the minimum {minimum}"
    cls = (st.facts or {}).get("_class")
    if cls and cls["value"] != sr.PROGRAM:
        out["program_mismatch"] = f"{code} is classed '{cls['value']}' for {app.program}"
    return out


def elective_credits_used(db: Session, app, exclude_row: str = "") -> int:
    used = 0
    for r in app.rows:
        if r["id"] == exclude_row:
            continue
        st = mp.get_state(db, app.id, r["id"])
        cls = (st.facts or {}).get("_class") if st else None
        if cls and cls["value"] == sr.ELECTIVE and st.mapping:
            used += st.mapping.get("aud_credits") or ac.transfer_credits()
    return used


def can_suggest_elective(db: Session, repo, app, row_id: str, st) -> str | None:
    """None if a Not-applicable equivalent may be suggested as a general elective, else the reason it may not."""
    code = (st.mapping or {}).get("aud_code")
    cls = (st.facts or {}).get("_class")
    if not code or not cls:
        return "This row has no mapped AUD course with a program classification."
    if cls["value"] != sr.NOT_APPLICABLE:
        return f"This row is already '{cls['value']}'."
    t = repo.targets.get(code)
    if t is None:
        return f"{code} is not in the AUD catalog."
    p = pr.Policy.load()
    if (t.get("department") or code.split()[0]).upper() in p.non_transferable_departments:
        return f"{code}: courses in this area never transfer."
    if t["level"] > 200:
        return f"{code} is above beginner level, so it cannot count as a general elective."
    left = sr.allowance_left(app.program, elective_credits_used(db, app, row_id))
    need = (st.mapping or {}).get("aud_credits") or ac.transfer_credits()
    if left is None:
        return "The student's program has no plan on file."
    if left < need:
        return f"Only {left} free-elective credits are left in the allowance, {need} are needed."
    return None
