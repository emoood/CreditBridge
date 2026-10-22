"""Code-only gates and lookups for the Case-agent path: no model calls here.

    relevance gate  ->  batch mapping lookup  ->  condition check  ->  policy check  ->  a row status

Statuses: Mapped | Conditional | No mapping | Waiting on document | Not evaluated.
No mapping, an unmet or unprovable condition, a stale mapping, or a rule failure are unresolved: the Case agent works those rows (the officer sees them as "Needs your input")."""
import json
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import CourseMapping, RowState, now
from ..services import grade_checks as gc
from ..services import policy_rules as pr
from .triage import ENFORCED

ROOT = Path(__file__).resolve().parents[3]
MAPPED, CONDITIONAL, NO_MAPPING, WAITING, NOT_EVAL = "Mapped", "Conditional", "No mapping", "Waiting on document", "Not evaluated"
WORKABLE = (NO_MAPPING, CONDITIONAL)  # the Case agent works these
MET, UNMET, MISSING = "MET", "UNMET", "MISSING"


# ---------------------------------------------------------------------------------------------------- relevance gate
def accepted_qualifications(curriculum: str | None = None) -> list[str]:
    d = yaml.safe_load((ROOT / "policies" / "curricula.yaml").read_text())
    return d["curricula"][curriculum or d["default"]]["accepted_qualifications"]


def relevance(row: dict[str, Any], curriculum: str | None = None) -> tuple[bool, str]:
    """Only externally examined qualifications the curriculum accepts are evaluated."""
    subj = row.get("subject")
    if not subj:
        return False, "Not in curriculum: no matching subject in the curriculum database"
    ok = {q.lower() for q in accepted_qualifications(curriculum)}
    if (subj.get("qualification") or "").lower() not in ok:
        return False, f"{subj.get('qualification') or 'This qualification'} is not an externally examined qualification accepted for this curriculum"
    return True, ""


def selection_rule(rows: list[dict[str, Any]], curriculum: str | None = None) -> str:
    """Why these rows are 'relevant', in words: e.g. 'AP courses, per AUD settings'."""
    d = yaml.safe_load((ROOT / "policies" / "curricula.yaml").read_text())
    accepted = accepted_qualifications(curriculum)
    present = {(r.get("subject") or {}).get("qualification") for r in rows}
    names = [d.get("short_names", {}).get(q, q) for q in accepted if q in present] or [d.get("short_names", {}).get(q, q) for q in accepted[:1]]
    inst = yaml.safe_load((ROOT / "policies" / "institution.yaml").read_text()).get("institution", "")
    return f"{' and '.join(names)} courses, per {inst} settings"


# ------------------------------------------------------------------------------------------------------- mapping table
def seed_mappings(db: Session) -> int:
    """Seed the DUMMY table from policies/dummy_mappings.yaml. Re-seeds when the file changed, but only if every row is still a dummy row
    (version starts with "dummy-"), so a real table is never overwritten."""
    data = yaml.safe_load((ROOT / "policies" / "dummy_mappings.yaml").read_text())["mappings"]
    rows = [{"qualification": "Advanced Placement", "aud_credits": 3, "version": "dummy-2026-10", "status": "ACTIVE", **{k: v for k, v in m.items() if v is not None}} for m in data]
    existing = list(db.scalars(select(CourseMapping)))
    key = lambda m: (m["subject_code"], m["aud_code"], m["status"], json.dumps(m.get("condition"), sort_keys=True))
    if existing:
        if not all(e.version.startswith("dummy-") for e in existing):
            return 0
        if sorted(key({"subject_code": e.subject_code, "aud_code": e.aud_code, "status": e.status, "condition": e.condition}) for e in existing) == sorted(key(r) for r in rows):
            return 0
        for e in existing:
            db.delete(e)
        db.flush()
    for r in rows:
        db.add(CourseMapping(**r))
    db.commit()
    return len(rows)


def mapping_dict(m: CourseMapping) -> dict[str, Any]:
    return {"id": m.id, "subject_code": m.subject_code, "aud_code": m.aud_code, "aud_credits": m.aud_credits, "condition": m.condition,
            "version": m.version, "status": m.status, "note": m.note}


def lookup_many(db: Session, codes: list[str]) -> dict[str, CourseMapping]:
    """One query for the whole transcript. If a subject has several rows, an ACTIVE one wins, then the newest id."""
    if not codes:
        return {}
    best: dict[str, CourseMapping] = {}
    for m in db.scalars(select(CourseMapping).where(CourseMapping.subject_code.in_(codes)).order_by(CourseMapping.id)):
        cur = best.get(m.subject_code)
        if cur is None or (m.status == "ACTIVE") >= (cur.status == "ACTIVE"):
            best[m.subject_code] = m
    return best


# ---------------------------------------------------------------------------------------------------------- conditions
def check_condition(cond: dict[str, Any] | None, row: dict[str, Any], facts: dict[str, Any], scale: dict[str, Any] | None = None) -> dict[str, Any]:
    """MET | UNMET | MISSING, with exactly what is missing. Deterministic."""
    if not cond:
        return {"status": MET, "reason": "No condition on this mapping.", "missing": []}
    t, v, ev = cond.get("type"), cond.get("value"), cond.get("evidence") or "supporting document"
    if t == "min_grade":
        letter, how = gc.letter_for_row(row, scale)  # the letter column if present, else the numeric mark through the transcript's printed scale
        ok = pr._grade_meets(pr.Policy.load(), letter or "", str(v))
        if ok is None:
            return {"status": MISSING, "reason": f"Needs a letter grade of at least {v}; the transcript shows '{row.get('grade')}', which cannot be placed on the grade scale.",
                    "missing": [{"field": "letter_grade", "evidence": "a transcript showing the letter grade or its grading scale"}]}
        return {"status": MET if ok else UNMET, "reason": f"Grade {letter} ({how}) {'meets' if ok else 'is below'} the required {v}.", "missing": []}
    if t == "min_exam_score":
        s = facts.get("exam_score")
        if s is None:
            return {"status": MISSING, "reason": f"Needs an exam score of at least {v}; none is on file.", "missing": [{"field": "exam_score", "evidence": ev}]}
        return {"status": MET if float(s) >= float(v) else UNMET, "reason": f"Exam score {s} {'meets' if float(s) >= float(v) else 'is below'} the required {v}.", "missing": []}
    if t == "exam_variant":
        s = facts.get("exam_variant")
        allowed = v if isinstance(v, list) else [v]
        if s is None:
            return {"status": MISSING, "reason": f"Only these exam variants map: {', '.join(map(str, allowed))}. The variant is not on file.",
                    "missing": [{"field": "exam_variant", "evidence": ev}]}
        return {"status": MET if str(s) in map(str, allowed) else UNMET, "reason": f"Exam variant {s} {'is' if str(s) in map(str, allowed) else 'is not'} an accepted variant.", "missing": []}
    return {"status": MISSING, "reason": f"Unknown condition type '{t}'.", "missing": [{"field": str(t), "evidence": ev}]}


# --------------------------------------------------------------------------------------------------------- row decision
def policy_fails(grade: str | None, aud_code: str, repo) -> list[str]:
    t = repo.targets.get(aud_code)
    if t is None:
        return [f"{aud_code} is not in the AUD catalog"]
    facts = pr.CourseFacts(grade, t["level"], t["restricted_type"], True, t.get("department") or aud_code.split()[0])
    return [f"{r.rule_id}: {r.reason}" for r in pr.evaluate_course(pr.Policy.load(), facts) if r.rule_id in ENFORCED and r.status == pr.FAIL]


def decide_row(row: dict[str, Any], mapping: CourseMapping | None, facts: dict[str, Any], override: bool, repo,
               curriculum: str | None = None, school_specific: bool = False, scale: dict[str, Any] | None = None) -> dict[str, Any]:
    """The code verdict for one row. Never calls a model."""
    out: dict[str, Any] = {"status": NOT_EVAL, "kind": "", "reason": "", "can_include": False, "mapping": None, "condition_result": None}
    if not row.get("included", True):
        return {**out, "kind": "left_out", "reason": "Left out at the extraction check"}
    if school_specific:
        return {**out, "kind": "school_specific", "reason": "School-specific subject: no university equivalent, so it is not mapped"}
    ok, why = relevance(row, curriculum)
    if not ok and not override:
        return {**out, "kind": "not_relevant", "reason": why, "can_include": True}
    if mapping is None:
        return {**out, "status": NO_MAPPING, "kind": "unmapped", "reason": "No mapping on file for this course"}
    out["mapping"] = mapping_dict(mapping)
    if mapping.status != "ACTIVE":
        return {**out, "status": CONDITIONAL, "kind": "stale", "reason": f"The mapping to {mapping.aud_code} is {mapping.status.lower()} (version {mapping.version}) and needs confirming"}
    cond = check_condition(mapping.condition, row, facts, scale)
    out["condition_result"] = cond
    if cond["status"] == UNMET:
        return {**out, "status": CONDITIONAL, "kind": "condition_unmet", "reason": cond["reason"]}
    if cond["status"] == MISSING:
        return {**out, "status": CONDITIONAL, "kind": "condition_missing", "reason": cond["reason"]}
    fails = policy_fails(row.get("grade"), mapping.aud_code, repo)
    if fails:
        return {**out, "status": CONDITIONAL, "kind": "rule_blocked", "reason": "Blocked by policy: " + "; ".join(fails)}
    return {**out, "status": MAPPED, "kind": "mapped", "reason": f"Mapped to {mapping.aud_code} (mapping {mapping.version})"}


def save_state(db: Session, app_id: str, row_id: str, **fields: Any) -> RowState:
    st = db.scalar(select(RowState).where(RowState.app_id == app_id, RowState.row_id == row_id))
    if st is None:
        st = RowState(app_id=app_id, row_id=row_id, status=fields.pop("status", NOT_EVAL), requests=[], facts={})
        db.add(st)
    for k, v in fields.items():
        setattr(st, k, v)
    st.updated_at = now()
    db.commit()
    return st


def get_state(db: Session, app_id: str, row_id: str) -> RowState | None:
    return db.scalar(select(RowState).where(RowState.app_id == app_id, RowState.row_id == row_id))


def state_dict(st: RowState) -> dict[str, Any]:
    return {"row_id": st.row_id, "status": st.status, "kind": st.kind, "reason": st.reason, "can_include": st.can_include,
            "included_override": st.included_override, "mapping": st.mapping, "condition_result": st.condition_result,
            "finding": st.finding, "requests": st.requests or [], "facts": st.facts or {}}
