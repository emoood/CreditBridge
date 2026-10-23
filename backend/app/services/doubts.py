"""Code checks behind the Case agent's whole-application review. The agent raises a doubt; code decides whether the
facts support it. A doubt of a named kind that code cannot corroborate is refused, so the agent cannot invent a problem. No model here."""
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import CourseMapping
from . import policy_rules as pr

KINDS = ("stale_mapping", "near_miss_grade", "other")


def newest_version(db: Session) -> str:
    return max((m.version for m in db.scalars(select(CourseMapping))), default="")


def signals(db: Session, repo, app, row: dict[str, Any], st) -> dict[str, Any]:
    """What code can see for one row: the facts the structured doubt kinds are checked against."""
    m = st.mapping or {}
    code = m.get("aud_code")
    out: dict[str, Any] = {"stale_mapping": None, "near_miss_grade": None}
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
    return out
