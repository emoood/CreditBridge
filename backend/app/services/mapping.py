"""Code-only gates and lookups for the Case-agent path: no model calls here.

    relevance gate  ->  batch mapping lookup  ->  condition check  ->  policy check  ->  a row status

Statuses: Mapped | Conditional | No mapping | Waiting on document | Not evaluated.
No mapping, an unmet or unprovable condition, a stale mapping, or a rule failure are unresolved: the Case agent works those rows (the officer sees them as "Needs your input")."""
import csv
import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import CourseMapping, RowState, now
from ..services import grade_checks as gc
from ..services import policy_rules as pr
from . import placement as pl
from . import student_record as sr
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
CSV_COLUMNS = ["source_course", "aud_code", "aud_name", "aud_credits", "min_exam_score", "notes", "version", "status", "detail_id"]
CSV_STATUS = {"active": "ACTIVE", "no_credit": "ACTIVE", "needs_review": "DRAFT", "no_equivalent": "NO_EQUIV"}
MARK = "[csv "  # marks a row loaded from mapping_ap.csv: a table made only of such rows (or dummy rows) may be re-seeded; anything else is a real table and is never overwritten
LAST_REPORT: dict[str, Any] = {"unmatched": [], "warnings": [], "rows": 0}
log = logging.getLogger("creditbridge.mapping")


def norm_name(t: str | None) -> str:
    """A course name as a key: case, a leading "AP" and punctuation ignored."""
    return "".join(ch for ch in re.sub(r"^\s*ap\b", "", (t or "").lower()) if ch.isalnum())


def _catalog() -> dict[str, str]:
    p = ROOT / "catalog" / "aud_undergrad_2026_2027" / "courses.csv"
    if not p.exists():
        return {}
    with p.open(newline="", encoding="utf-8") as f:
        return {r["code"].strip(): r.get("credits", "").strip() for r in csv.DictReader(f)}


def read_mapping_csv(path: Path | None = None) -> list[dict[str, Any]]:
    """Read and validate policies/data/mapping_ap.csv. Raises ValueError naming the file and row; warnings go to LAST_REPORT."""
    path = path or ROOT / "policies" / "data" / "mapping_ap.csv"
    name = f"{path.parent.name}/{path.name}"
    with path.open(newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        missing = [c for c in CSV_COLUMNS if c not in (rd.fieldnames or [])]
        if missing:
            raise ValueError(f"{name}: missing columns {missing}")
        rows = list(rd)
    seen: dict[tuple[str, str], int] = {}
    cat, warnings, out = _catalog(), [], []
    for n, r in enumerate(rows, start=2):  # line 1 is the header
        st = (r["status"] or "").strip().lower()
        if st not in CSV_STATUS:
            raise ValueError(f"{name} row {n}: unknown status '{r['status']}'")
        if not (r["source_course"] or "").strip():
            raise ValueError(f"{name} row {n}: empty source_course")
        key = (norm_name(r["source_course"]), (r["aud_code"] or "").strip())
        if key in seen:
            raise ValueError(f"{name} row {n}: duplicate (source_course, aud_code) {key}, first seen on row {seen[key]}")
        seen[key] = n
        code = (r["aud_code"] or "").strip()
        credits = int(float(r["aud_credits"])) if (r["aud_credits"] or "").strip() else 0
        if code and cat and code not in cat:
            warnings.append(f"row {n}: {code} is not in the AUD catalog")
        elif code and cat.get(code) not in (None, "") and credits and float(cat[code]) != credits:
            warnings.append(f"row {n}: {code} has {credits} credits in the mapping but {cat[code]} in the catalog")
        out.append({**{k: (r[k] or "").strip() for k in CSV_COLUMNS}, "status": CSV_STATUS[st], "kind": st, "aud_credits": credits, "line": n})
    LAST_REPORT["warnings"] = warnings
    return out


def _overlay() -> list[dict[str, Any]]:
    p = ROOT / "policies" / "data" / "mapping_conditions.yaml"
    return (yaml.safe_load(p.read_text()) or {}).get("conditions", []) if p.exists() else []


def seed_mappings(db: Session) -> int:
    """Load policies/data/mapping_ap.csv into course_mappings (plus the condition overlay). Each source course is matched to a curriculum subject
    (qualification Advanced Placement); without a match the row is keyed "name:<normalized name>". Re-seeds when the file changed, but only if every
    existing row is a dummy row or a CSV row, so a real table is never overwritten."""
    rows = read_mapping_csv()
    from .curriculum import curriculum as cur  # lazy: the curriculum service is optional at load time
    key_of: dict[str, str] = {}
    unmatched: list[str] = []
    for src in sorted({r["source_course"] for r in rows}):
        hit = None
        try:
            hit = next(iter(cur.candidates(src if src.lower().startswith("ap ") else f"AP {src}", "", "Advanced Placement", 1)), None)
        except Exception:  # curriculum database unreachable: key by name; the lookup also tries the name
            hit = None
        if hit and hit["score"] >= 0.75:
            key_of[src] = hit["code"]
        else:
            key_of[src] = "name:" + norm_name(src)
            unmatched.append(src)
    LAST_REPORT.update(unmatched=unmatched, rows=len(rows))
    over = _overlay()
    new = []
    for r in rows:
        cond = None
        for o in over:
            if norm_name(o["source_course"]) == norm_name(r["source_course"]):
                cond = {**o["condition"], **({"programs": o["programs"]} if o.get("programs") else {})}
        note = " · ".join(x for x in (r["notes"], r["aud_name"]) if x)
        new.append({"qualification": "Advanced Placement", "subject_code": key_of[r["source_course"]], "subject_name": r["source_course"], "aud_code": r["aud_code"],
                    "aud_credits": r["aud_credits"], "condition": cond, "version": r["version"], "status": r["status"], "programs": None,
                    "note": f"{MARK}{r['detail_id']}] {note}".strip()})
    existing = list(db.scalars(select(CourseMapping)))
    sig = lambda m: (m["subject_code"], m["aud_code"], m["aud_credits"], m["status"], json.dumps(m.get("condition"), sort_keys=True), m["note"])
    if existing:
        if not all(e.version.startswith("dummy-") or e.note.startswith(MARK) for e in existing):
            return 0
        if sorted(sig({"subject_code": e.subject_code, "aud_code": e.aud_code, "aud_credits": e.aud_credits, "status": e.status, "condition": e.condition, "note": e.note}) for e in existing) == sorted(sig(r) for r in new):
            return 0
        for e in existing:
            db.delete(e)
        db.flush()
    for r in new:
        db.add(CourseMapping(**r))
    db.commit()
    for w in LAST_REPORT["warnings"]:
        log.warning("mapping_ap.csv %s", w)
    for u in unmatched:
        log.warning("mapping_ap.csv: no curriculum subject matches '%s'; keyed by name", u)
    return len(new)


def mapping_dict(m: CourseMapping) -> dict[str, Any]:
    return {"programs": m.programs, "id": m.id, "subject_code": m.subject_code, "aud_code": m.aud_code, "aud_credits": m.aud_credits, "condition": m.condition,
            "version": m.version, "status": m.status, "note": m.note}


def lookup_many(db: Session, keys: list[str], program: str = "") -> dict[str, list[CourseMapping]]:
    """One query for the whole transcript: every mapping row of each key (a subject code, or "name:<normalized name>"), in table order.
    A mapping limited to the student's program replaces the general rows of that key."""
    if not keys:
        return {}
    out: dict[str, list[CourseMapping]] = {}
    for m in db.scalars(select(CourseMapping).where(CourseMapping.subject_code.in_(keys)).order_by(CourseMapping.id)):
        if m.programs and program not in m.programs:
            continue
        out.setdefault(m.subject_code, []).append(m)
    for k, v in out.items():
        if any(m.programs for m in v):
            out[k] = [m for m in v if m.programs]
    return out


def keys_for(row: dict[str, Any]) -> list[str]:
    """The table keys for a transcript row: its curriculum subject code, then its normalized names."""
    subj = row.get("subject") or {}
    return [k for k in (subj.get("code"), "name:" + norm_name(subj.get("name")) if subj.get("name") else None, "name:" + norm_name(row.get("title"))) if k]


def choose(maps: dict[str, list[CourseMapping]], row: dict[str, Any], program: str) -> CourseMapping | None:
    """The mapping row that fits the student's plan best: one that fills a core slot, then a Big Questions slot, then BQ-ELE, then GEN-ELE; ties go to credits, then table order."""
    cands = next((maps[k] for k in keys_for(row) if maps.get(k)), [])
    if not cands:
        return None
    def rank(m: CourseMapping) -> tuple:
        o = pl.options(program, m.aud_code)
        return (0 if m.status == "ACTIVE" else 1, (o and (0 if o[0] == pl.CORE else 1 if o[0] != pl.GEN_ELE and o[0] != pl.BQ_ELE else 2 if o[0] == pl.BQ_ELE else 3)) if o else 9, -m.aud_credits, m.id)
    return min(cands, key=rank)


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


def synopsis_for(row: dict[str, Any], synopsis: dict[str, str] | None) -> str | None:
    """The synopsis-page description for this course: an exact match on the normalized course name (case, a leading "AP" and punctuation ignored), or None."""
    norm = lambda t: "".join(ch for ch in re.sub(r"^\s*ap\b", "", (t or "").lower()) if ch.isalnum())
    me = norm(row.get("title"))
    return next((d for t, d in (synopsis or {}).items() if me and norm(t) == me), None)


def decide_row(row: dict[str, Any], mapping: CourseMapping | None, facts: dict[str, Any], override: bool, repo,
               curriculum: str | None = None, school_specific: bool = False, scale: dict[str, Any] | None = None,
               synopsis: dict[str, str] | None = None, program: str = "") -> dict[str, Any]:
    """The code verdict for one row. Never calls a model."""
    out: dict[str, Any] = {"status": NOT_EVAL, "kind": "", "reason": "", "can_include": False, "mapping": None, "condition_result": None}
    if not row.get("included", True):
        return {**out, "kind": "left_out", "reason": "Left out at the extraction check"}
    if school_specific:
        return {**out, "kind": "school_specific", "reason": "School-specific subject: no university equivalent, so it is not mapped"}
    ok, why = relevance(row, curriculum)
    if not ok and not override:
        return {**out, "kind": "not_relevant", "reason": why, "can_include": True}
    pol = pr.Policy.load()
    if pr._grade_meets(pol, (row.get("grade") or ""), pol.min_grade_lower) is False:  # minimum grade C: below it nothing transfers, and the officer cannot include it
        return {**out, "kind": "not_applicable", "reason": "Grade below C, not eligible for transfer credit"}
    if mapping is None:
        return {**out, "status": NO_MAPPING, "kind": "unmapped", "reason": "No mapping on file for this course"}
    out["mapping"] = mapping_dict(mapping)
    if mapping.status == "NO_EQUIV":
        return {**out, "kind": "not_applicable", "reason": "No AUD equivalent"}
    if mapping.status != "ACTIVE":  # a mapping that needs review is never auto-placed: the officer sees it first
        return {**out, "status": CONDITIONAL, "kind": "stale", "reason": "This mapping needs review"}
    why_not = sr.restriction(mapping.aud_code, program)
    if why_not and not override:  # applicability comes BEFORE the conditions: a course the plan has no place for needs no document and no investigation
        return {**out, "status": NOT_EVAL, "kind": "not_applicable", "reason": why_not, "can_include": True}
    cond_def = mapping.condition
    if cond_def and cond_def.get("programs") and program not in cond_def["programs"]:
        cond_def = None
    cond = check_condition(cond_def, row, facts, scale)
    out["condition_result"] = cond
    if cond["status"] == UNMET:
        return {**out, "status": CONDITIONAL, "kind": "condition_unmet", "reason": cond["reason"]}
    if cond["status"] == MISSING:
        return {**out, "status": CONDITIONAL, "kind": "condition_missing", "reason": cond["reason"]}
    if synopsis and not synopsis_for(row, synopsis) and not facts.get("syllabus"):  # the school sent a synopsis page: a course missing from it needs its syllabus
        miss = {"status": MISSING, "reason": "The school's courses synopsis has no entry for this course; its syllabus is needed.",
                "missing": [{"field": "syllabus", "evidence": "course syllabus from the school"}]}
        return {**out, "condition_result": miss, "status": CONDITIONAL, "kind": "condition_missing", "reason": miss["reason"]}
    fails = policy_fails(row.get("grade"), mapping.aud_code, repo)
    if fails:
        return {**out, "status": CONDITIONAL, "kind": "rule_blocked", "reason": "Blocked by policy: " + "; ".join(fails)}
    return {**out, "status": MAPPED, "kind": "mapped",
                "reason": "Mapped, no credit awarded" if mapping.aud_credits == 0 else f"Mapped to {mapping.aud_code} (mapping {mapping.version})"}


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


def merge_facts(db: Session, app_id: str, row_id: str, **kv: Any) -> None:
    """Add/replace keys in the row's facts without touching the others (agent judgments live under keys starting with an underscore)."""
    st = get_state(db, app_id, row_id)
    if st is not None:
        save_state(db, app_id, row_id, facts={**(st.facts or {}), **kv})


def get_state(db: Session, app_id: str, row_id: str) -> RowState | None:
    return db.scalar(select(RowState).where(RowState.app_id == app_id, RowState.row_id == row_id))


def state_dict(st: RowState) -> dict[str, Any]:
    return {"row_id": st.row_id, "status": st.status, "kind": st.kind, "reason": st.reason, "can_include": st.can_include,
            "included_override": st.included_override, "mapping": st.mapping, "condition_result": st.condition_result,
            "finding": st.finding, "requests": st.requests or [], "facts": st.facts or {},
            "classification": (st.facts or {}).get("_class"), "doubts": (st.facts or {}).get("_doubts", [])}
