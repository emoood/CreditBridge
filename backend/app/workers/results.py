"""Turn a CaseResult into the JSON the UI shows. Contains curriculum/catalog text and agent output, never raw transcript text."""
from typing import Any

from ..agents.orchestrator import CaseResult
from ..runtime import rt
from ..services import policy_rules as pr

STATIC_ONLY = {"AUD_MIN_LEVEL", "AUD_GRADE_LOWER", "AUD_GRADE_UPPER", "AUD_RESTRICTED_COURSE_TYPES"}


def _excerpt(i: str) -> dict[str, str]:
    ex = rt().repo.store.get(i)
    return {"id": i, "text": ex.text if ex else ""}


def _target(code: str | None) -> dict[str, Any] | None:
    t = rt().repo.targets.get(code or "")
    return t and {"code": t["code"], "title": t["title"], "level": t["level"], "credits": t["credits"], "basis": t.get("evidence_basis")}


def course_row(sid: str, row: dict[str, Any]) -> dict[str, Any]:
    src = rt().repo.sources.get(sid)
    subj = row.get("subject")
    return {"code": sid, "row_code": row.get("code") or "", "title": (src.title if src else row.get("title")) or "",
            "title_ar": row.get("title_ar") or "", "credits": str(row.get("credits") or ""), "grade": row.get("grade") or "",
            "term": row.get("term") or "", "has_syllabus": bool(src and src.syllabus_doc_id),
            "curriculum_subject": subj["name"] if subj else None, "curriculum_qualification": subj["qualification"] if subj else None}


def serialize(r: CaseResult, row: dict[str, Any], secs: float, result_id: str) -> dict[str, Any]:
    S = rt()
    m = r.match.output
    target = _target(m.target_course_id) if m and m.target_course_id else None
    staff = []
    if r.match.status == "POTENTIAL_MATCH" and target:
        staff = [{"rule_id": x.rule_id, "status": x.status, "reason": x.reason} for x in
                 pr.evaluate_course(pr.Policy.load(), pr.CourseFacts(None, None, None, False)) if x.rule_id not in STATIC_ONLY]
    return {
        "result_id": result_id, "course": course_row(r.source_course_id, row),
        "triage": {"category": r.triage.category, "reasons": r.triage.reasons, "priority": r.triage.priority},
        "match": {"status": r.match.status, "target": target, "evidence_strength": m.evidence_strength if m else None,
                  "supported_outcomes": [{"subject": _excerpt(o.source_excerpt_id), "target": _excerpt(o.target_excerpt_id),
                                          "explanation": o.explanation} for o in (m.supported_outcomes if m else [])],
                  "missing_or_weaker_outcomes": m.missing_or_weaker_outcomes if m else [],
                  "title_only_suggestions": [{"target": _target(t.target_course_id), "reason": t.reason, "basis": "TITLE_ONLY_UNVERIFIED"}
                                             for t in (m.title_only_suggestions if m else [])], "concerns": m.concerns if m else [],
                  "flags": r.match.flags},
        "review": None if not r.review else {
            "status": r.review.status, "flags": r.review.flags,
            "objections": [{"text": o.text, "excerpts": [_excerpt(i) for i in o.excerpt_ids]}
                           for o in (r.review.output.objections if r.review.output else [])],
            "notes": list(r.review.output.notes) if r.review.output else []},
        "rules": [{"rule_id": x.rule_id, "status": x.status, "reason": x.reason, "source_url": x.policy_source_url}
                  for x in r.rules if x.status != "NOT_APPLICABLE"],
        "staff_checklist": staff, "case_file": r.case.to_dict(), "llm_calls": r.llm_calls, "provider": r.provider, "model": r.model, "prompt_versions": r.prompt_versions,
        "duration_s": round(secs, 1), "agent_flags": r.flags,
        "syllabus_requests": [q for q in S.repo.syllabus_requests if q["course_id"] == r.source_course_id][-1:],
        "is_replay": False,
    }
