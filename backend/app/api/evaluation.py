"""Evaluation API. Agents suggest; only an `admissions` officer decides. Works for the demo fixtures and for uploads."""
import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..agents.orchestrator import CaseResult, run_case
from ..agents.precedent import Precedent
from ..config import get_settings
from ..devlog import events
from ..providers.router import build_provider
from ..services import policy_rules as pr
from ..services.curriculum import curriculum
from . import state as st
from .state import AppState

router = APIRouter(prefix="/api/v1")
DECIDER_ROLE = "admissions"
STATIC_ONLY = {"AUD_MIN_LEVEL", "AUD_GRADE_LOWER", "AUD_GRADE_UPPER", "AUD_RESTRICTED_COURSE_TYPES"}


def get_app(app_id: str) -> AppState:
    app = st.S.apps.get(app_id)
    if not app:
        raise HTTPException(404, "Unknown application")
    return app


def app_summary(app: AppState) -> dict[str, Any]:
    job = st.S.jobs.get(app.latest_job) if app.latest_job else None
    decided = sum(1 for v in app.decisions.values() if v)
    if job:
        status = "EVALUATION_IN_PROGRESS" if job["status"] in ("RUNNING", "QUEUED") else "REVIEW_READY"
        if app.results and decided == len(app.results):
            status = "DECISIONS_RECORDED"
    else:
        status = "READY_FOR_EVALUATION" if app.kind == "demo" else "EXTRACTION_REVIEW_REQUIRED"
    return {"id": app.id, "kind": app.kind, "student": app.student, "program": app.program, "institution": app.institution,
            "courses": sum(1 for r in app.rows if r["included"]), "status": status, "decided": decided,
            "catalog": st.S.repo.catalog_label, "created_at": app.created_at}


def _excerpt(i: str) -> dict[str, str]:
    ex = st.S.repo.store.get(i)
    return {"id": i, "text": ex.text if ex else ""}


def _course_row(sid: str, app: AppState) -> dict[str, Any]:
    c = st.S.repo.sources[sid]
    row = next((r for r in app.rows if r.get("source_id") == sid or r["id"] == sid), {})
    subj = row.get("subject")
    return {"code": sid, "row_code": row.get("code") or "", "title": c.title, "title_ar": c.title_ar, "credits": c.credits,
            "grade": c.grade, "term": c.term, "has_syllabus": c.syllabus_doc_id is not None,
            "curriculum_subject": subj["name"] if subj else None, "curriculum_qualification": subj["qualification"] if subj else None}


def _claim(c: dict[str, Any] | None) -> dict[str, Any] | None:
    if not c:
        return None
    t = st.S.repo.targets.get(c["target_course_id"] or "")
    return {"target": t and {"code": t["code"], "title": t["title"], "level": t["level"], "credits": t["credits"]},
            "evidence_strength": c["evidence_strength"], "objections": c["objections"],
            "supported_outcomes": [{"subject": _excerpt(o["source_excerpt_id"]), "target": _excerpt(o["target_excerpt_id"]),
                                    "explanation": o["explanation"]} for o in c["supported_outcomes"]]}


def _serialize(app: AppState, r: CaseResult, secs: float) -> dict[str, Any]:
    S = st.S
    m = r.match.output
    target = None
    if m and m.target_course_id and m.target_course_id in S.repo.targets:
        t = S.repo.targets[m.target_course_id]
        target = {"code": t["code"], "title": t["title"], "level": t["level"], "credits": t["credits"]}
    staff = []
    if r.match.status == "POTENTIAL_MATCH" and target:
        staff = [{"rule_id": x.rule_id, "status": x.status, "reason": x.reason} for x in
                 pr.evaluate_course(pr.Policy.load(), pr.CourseFacts(None, None, None, False)) if x.rule_id not in STATIC_ONLY]
    return {
        "course": _course_row(r.source_course_id, app),
        "triage": {"category": r.triage.category, "reasons": r.triage.reasons, "priority": r.triage.priority},
        "match": {
            "status": r.match.status, "target": target, "evidence_strength": m.evidence_strength if m else None,
            "supported_outcomes": [{"subject": _excerpt(o.source_excerpt_id), "target": _excerpt(o.target_excerpt_id),
                                    "explanation": o.explanation} for o in (m.supported_outcomes if m else [])],
            "missing_or_weaker_outcomes": m.missing_or_weaker_outcomes if m else [],
            "concerns": m.concerns if m else [], "response_to_objections": m.response_to_objections if m else None,
            "flags": r.match.flags},
        "review": None if not r.review else {
            "status": r.review.status, "flags": r.review.flags,
            "objections": [{"text": o.text, "excerpts": [_excerpt(i) for i in o.excerpt_ids]}
                           for o in (r.review.output.objections if r.review.output else [])]},
        "rules": [{"rule_id": x.rule_id, "status": x.status, "reason": x.reason, "source_url": x.policy_source_url}
                  for x in r.rules if x.status != "NOT_APPLICABLE"],
        "staff_checklist": staff, "case_file": r.case.to_dict(), "revised": r.revised, "contested": r.contested,
        "llm_calls": r.llm_calls, "provider": r.provider, "model": r.model, "prompt_versions": r.prompt_versions,
        "duration_s": round(secs, 1), "agent_flags": r.flags,
        "syllabus_requests": [q for q in S.repo.syllabus_requests if q["course_id"] == r.source_course_id][-1:],
        "original_claim": _claim(r.original_claim), "is_replay": False, "decision": None, "version": 1,
    }


async def _prepare_sources(app: AppState, job: dict[str, Any]) -> list[str]:
    """Register each included row as a source course. Upload evidence = the linked curriculum subject's outcomes."""
    S = st.S
    if app.kind == "demo":
        return list(S.repo.sources)
    sids = []
    n = 0
    for row in app.rows:
        if not row["included"]:
            continue
        n += 1
        sid = f"{app.short}-{n:02d}"
        outcomes: list[dict] = []
        subj = row.get("subject")
        if subj:
            try:
                outcomes = await asyncio.to_thread(curriculum.outcomes, subj["id"])
            except Exception as e:  # noqa: BLE001
                job["errors"][sid] = f"Curriculum lookup failed: {type(e).__name__}"
        title = row["title"] or (subj["name"] if subj else "") or row["title_ar"] or "Untitled subject"
        S.repo.add_source(sid, title, str(row["credits"] or ""), row["grade"] or "", row["term"], row["title_ar"],
                          outcomes, app.document["id"] if app.document else "")
        row["source_id"] = sid
        sids.append(sid)
    return sids


async def _run_job(app: AppState, job_id: str) -> None:
    S = st.S
    s = get_settings()
    job = S.jobs[job_id]
    try:
        provider = build_provider(s)
        job["status"] = "RUNNING"
        sids = await _prepare_sources(app, job)
        job["courses"] = dict.fromkeys(sids, "QUEUED")
        for sid in sids:
            job["courses"][sid] = "RUNNING"
            t0 = time.time()
            try:
                r = await run_case(provider, S.repo, S.retriever, sid, S.precedent,
                                   s.matcher_max_tool_rounds, s.reviewer_max_tool_rounds)
                app.results[sid] = _serialize(app, r, time.time() - t0)
                job["courses"][sid] = "DONE"
                S.log("system", "case_completed", sid, triage=r.triage.category, llm_calls=r.llm_calls)
            except Exception as e:  # noqa: BLE001  retryable per course; other courses continue
                job["courses"][sid] = "FAILED_RETRYABLE"
                job["errors"][sid] = f"{type(e).__name__}: {str(e)[:160]}"
                events.emit("error", where="case", course=sid, error=job["errors"][sid])
        job["status"] = "DONE"
    except Exception as e:  # noqa: BLE001
        job["status"] = "DONE"
        job["errors"]["job"] = f"{type(e).__name__}: {str(e)[:160]}"
    finally:
        job["finished_at"] = time.time()
        S.active_job = None


@router.get("/applications")
def list_applications():
    return [app_summary(a) for a in st.S.apps.values()]


@router.post("/applications/{app_id}/evaluate")
async def evaluate(app_id: str, x_role: str | None = Header(default=None)):
    S = st.S
    app = get_app(app_id)
    if S.active_job:
        raise HTTPException(409, "An evaluation is already running")
    if not any(r["included"] for r in app.rows):
        raise HTTPException(400, "There are no subjects to evaluate")
    job_id = uuid.uuid4().hex[:12]
    app.results.clear()
    app.decisions.clear()
    S.jobs[job_id] = {"id": job_id, "app_id": app_id, "status": "QUEUED", "started_at": time.time(), "finished_at": None,
                      "courses": {}, "errors": {}}
    S.active_job = app.latest_job = job_id
    S.log(x_role or "unknown", "evaluation_started", app_id, job=job_id)
    asyncio.create_task(_run_job(app, job_id))
    return {"job_id": job_id}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = st.S.jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Unknown job")
    since = job["started_at"]
    activity = [{"ts": e["ts"], "kind": e["kind"], "course": e.get("course"), "tool": e.get("tool"),
                 "stage": e.get("stage"), "role": e.get("role"), "ok": e.get("ok"), "duration_s": e.get("duration_s")}
                for e in events.recent(400) if e["ts"] >= since and e["kind"] in ("tool_call", "stage", "llm_call", "error")]
    s = get_settings()
    return {**job, "route": s.llm_default_route, "model": s.route(s.llm_default_route)[1],
            "llm_calls": sum(1 for a in activity if a["kind"] == "llm_call"), "activity": activity[-25:]}


@router.get("/applications/{app_id}/results")
def get_results(app_id: str):
    app = get_app(app_id)
    rows = []
    for sid, r in app.results.items():
        d = app.decisions.get(sid, [])
        rows.append({**r, "decision": d[-1] if d else None, "version": len(d) + 1})
    rows.sort(key=lambda r: r["triage"]["priority"])
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["triage"]["category"]] = counts.get(r["triage"]["category"], 0) + 1
    return {"job": st.S.jobs.get(app.latest_job) if app.latest_job else None, "results": rows, "counts": counts,
            "catalog": st.S.repo.catalog_label}


class DecisionIn(BaseModel):
    decision: Literal["ACCEPTED", "DECLINED", "MORE_INFORMATION_REQUESTED"]
    reason: str = ""
    expected_version: int
    idempotency_key: str


@router.post("/applications/{app_id}/results/{course}/decision")
def decide(app_id: str, course: str, body: DecisionIn, x_role: str | None = Header(default=None),
           x_actor: str | None = Header(default=None)):
    if x_role != DECIDER_ROLE:  # enforced here, not in the UI
        raise HTTPException(403, "Only an admissions officer can record a decision")
    app = get_app(app_id)
    r = app.results.get(course)
    if not r:
        raise HTTPException(404, "No result for this course")
    history = app.decisions.setdefault(course, [])
    for d in history:
        if d["idempotency_key"] == body.idempotency_key:
            return d  # replay: no duplicate decision
    if body.expected_version != len(history) + 1:
        raise HTTPException(409, "This result changed; reload and try again")
    if body.decision in ("DECLINED", "MORE_INFORMATION_REQUESTED") and not body.reason.strip():
        raise HTTPException(422, "A reason is required")
    if body.decision == "ACCEPTED":
        if r["triage"]["category"] == "BLOCKED_BY_RULE":
            raise HTTPException(409, "Blocked by a policy rule; it cannot be accepted")
        if r["match"]["status"] not in ("POTENTIAL_MATCH", "POTENTIAL_GENERAL_ELECTIVE"):
            raise HTTPException(409, "There is no suggestion to accept for this course")
    rec = {"decision": body.decision, "reason": body.reason.strip(), "actor": x_actor or "admissions officer",
           "actor_role": x_role, "idempotency_key": body.idempotency_key, "at": datetime.now(UTC).isoformat()}
    history.append(rec)
    if body.decision in ("ACCEPTED", "DECLINED"):
        t = r["match"]["target"]
        st.S.precedent.add(Precedent(r["course"]["title"], r["course"].get("curriculum_qualification") or "demo",
                                     t["code"] if t else None, body.decision, rec["reason"], rec["actor"], rec["at"]))
    st.S.log(rec["actor"], f"decision_{body.decision.lower()}", f"{app_id}/{course}", reason=rec["reason"])
    return rec


@router.get("/applications/{app_id}/audit")
def audit(app_id: str):
    get_app(app_id)
    return st.S.audit[-200:]


@router.post("/dev/reset")
def reset():
    if not get_settings().dev_console:
        raise HTTPException(404)
    if st.S.active_job:
        raise HTTPException(409, "An evaluation is running")
    st.reset_state()
    events.clear()
    return {"reset": True}
