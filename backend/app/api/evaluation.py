"""Evaluation, jobs, results and human decisions. Agents suggest; only an `admissions` officer decides."""
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select

from ..agents.precedent import Precedent
from ..config import get_settings
from ..db.models import (
    Application,
    AuditEvent,
    Decision,
    Job,
    JobUnit,
    ModelLog,
    RowAction,
    RowState,
    TraceEvent,
    UsageEntry,
    iso,
)
from ..db.session import app_session
from ..devlog import events
from ..runtime import reset_runtime, rt
from ..services import actions as ac
from ..services import mapping as mp
from ..workers import store
from ..workers.queue import worker
from .common import DECIDER_ROLE, app_summary, get_app, job_dict, seed_demo

router = APIRouter(prefix="/api/v1")
FINISHED = ("REVIEW_READY", "EVALUATION_PARTIAL")


def _active(db) -> Job | None:
    return db.scalar(select(Job).where(Job.status.in_(["QUEUED", "RUNNING"])).limit(1))


@router.post("/applications/{app_id}/evaluate")
def evaluate(app_id: str, x_role: str | None = Header(default=None)):
    """Start (or re-run) Phase 2. For an uploaded transcript this is refused until the officer has confirmed the extraction."""
    with app_session() as db:
        app = get_app(db, app_id)
        if app.kind == "upload" and app.confirmed_at is None:
            raise HTTPException(409, "Confirm the extraction first: evaluation never starts automatically")
        if app.kind == "upload" and app.status not in (*FINISHED, "CONFIRMED", "EVALUATION_IN_PROGRESS", "DECISIONS_RECORDED"):
            raise HTTPException(409, f"Cannot evaluate while the application is {app.status}")
        if db.scalar(select(Job).where(Job.app_id == app_id, Job.phase == "EVALUATION", Job.status.in_(["QUEUED", "RUNNING"]))):
            raise HTTPException(409, "An evaluation is already queued or running for this application")
        if not any(r.get("included") for r in app.rows):
            raise HTTPException(400, "There are no subjects to evaluate")
        job = Job(id=uuid.uuid4().hex[:12], app_id=app_id, phase="EVALUATION", status="QUEUED")
        db.add(job)
        app.evaluation_job_id = job.id
        if app.kind == "demo":
            app.status = "READY_FOR_EVALUATION"
        db.commit()
        store.audit(db, app_id, x_role or "unknown", "evaluation_requested", job.id, queue_position=db.query(Job).filter(Job.status == "QUEUED").count())
    worker.enqueue(job.id)
    return {"job_id": job.id}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    with app_session() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        return job_dict(db, job)


@router.get("/applications/{app_id}/jobs")
def list_jobs(app_id: str):
    with app_session() as db:
        get_app(db, app_id)
        return [job_dict(db, j) for j in db.scalars(select(Job).where(Job.app_id == app_id).order_by(Job.created_at.desc()))]


@router.post("/jobs/{job_id}/resume")
def resume(job_id: str, x_role: str | None = Header(default=None)):
    """Continue an INTERRUPTED/FAILED job (or one that finished with failed units) from the last DONE unit."""
    with app_session() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Unknown job")
        app = get_app(db, job.app_id)
        has_failed = db.scalar(select(JobUnit).where(JobUnit.job_id == job.id, JobUnit.status.in_(["FAILED_RETRYABLE", "PENDING", "RUNNING"])).limit(1))
        if job.status in ("QUEUED", "RUNNING"):
            raise HTTPException(409, "The job is already queued or running")
        if job.status == "DONE" and not has_failed:
            raise HTTPException(409, "Nothing to resume: every unit is done")
        if job.phase == "READING" and any(r.get("corrected") for r in app.rows):
            raise HTTPException(409, "Cannot resume reading after the officer has made corrections")
        job.status, job.finished_at = "QUEUED", None
        job.summary = {**job.summary, "resumed": True}
        job.flags = [f for f in job.flags if f != "INTERRUPTED_BY_RESTART"]
        db.commit()
        store.audit(db, job.app_id, x_role or "unknown", "job_resumed", job.id, phase=job.phase)
    worker.enqueue(job_id)
    return {"job_id": job_id, "status": "QUEUED"}


@router.get("/applications/{app_id}/results")
def get_results(app_id: str):
    with app_session() as db:
        app = get_app(db, app_id)
        units = list(db.scalars(select(JobUnit).where(JobUnit.app_id == app_id, JobUnit.stage == "evaluate").order_by(JobUnit.seq)))
        decisions: dict[str, list[Decision]] = {}
        for d in db.scalars(select(Decision).where(Decision.app_id == app_id).order_by(Decision.id)):
            decisions.setdefault(d.unit_key, []).append(d)
        rows, failed = [], []
        for u in units:
            if u.status == "DONE" and u.result:
                hist = decisions.get(u.unit_key, [])
                last = hist[-1] if hist else None
                dec = last and {"decision": last.decision, "reason": last.reason, "actor": last.actor, "actor_role": last.actor_role,
                                "at": iso(last.at), "idempotency_key": last.idempotency_key,
                                "stale": bool(last.result_id and last.result_id != u.result.get("result_id"))}
                rows.append({**u.result, "decision": dec, "version": len(hist) + 1})
            elif u.status in ("FAILED_RETRYABLE", "PENDING", "RUNNING"):
                failed.append({"key": u.unit_key, "status": u.status, "error_code": u.error_code})
        rows.sort(key=lambda r: r["triage"]["priority"])
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["triage"]["category"]] = counts.get(r["triage"]["category"], 0) + 1
        job = db.get(Job, app.evaluation_job_id) if app.evaluation_job_id else None
        states = {s.row_id: mp.state_dict(s) for s in db.scalars(select(RowState).where(RowState.app_id == app_id))}
        actions = {a.row_id: ac.action_dict(a) for a in ac.latest(db, app_id).values()}
        return {"states": states, "actions": actions, "selection_rule": mp.selection_rule(app.rows), "results": rows, "counts": counts, "pending_units": failed, "catalog": rt().repo.catalog_label,
                "job": job_dict(db, job) if job else None, "status": app_summary(db, app)["status"]}


class DecisionIn(BaseModel):
    decision: Literal["ACCEPTED", "DECLINED", "MORE_INFORMATION_REQUESTED"]
    reason: str = ""
    expected_version: int
    idempotency_key: str


@router.post("/applications/{app_id}/results/{course}/decision")
def decide(app_id: str, course: str, body: DecisionIn, x_role: str | None = Header(default=None), x_actor: str | None = Header(default=None)):
    if x_role != DECIDER_ROLE:  # enforced here, not in the UI
        raise HTTPException(403, "Only an admissions officer can record a decision")
    with app_session() as db:
        get_app(db, app_id)
        existing = db.scalar(select(Decision).where(Decision.idempotency_key == body.idempotency_key))
        if existing:  # replay: no duplicate decision
            return {"decision": existing.decision, "reason": existing.reason, "actor": existing.actor, "actor_role": existing.actor_role,
                    "idempotency_key": existing.idempotency_key, "at": iso(existing.at)}
        u = store.get_unit(db, app_id, "evaluate", course)
        if not u or u.status != "DONE" or not u.result:
            raise HTTPException(404, "No result for this course")
        r: dict[str, Any] = u.result
        n_prev = db.query(Decision).filter(Decision.app_id == app_id, Decision.unit_key == course).count()
        if body.expected_version != n_prev + 1:
            raise HTTPException(409, "This result changed; reload and try again")
        if body.decision in ("DECLINED", "MORE_INFORMATION_REQUESTED") and not body.reason.strip():
            raise HTTPException(422, "A reason is required")
        if body.decision == "ACCEPTED":
            if r["triage"]["category"] == "BLOCKED_BY_RULE":
                raise HTTPException(409, "Blocked by a policy rule; it cannot be accepted")
            if r["match"]["status"] not in ("POTENTIAL_MATCH", "POTENTIAL_GENERAL_ELECTIVE"):
                raise HTTPException(409, "There is no suggestion to accept for this course")
        t = r["match"]["target"]
        d = Decision(app_id=app_id, unit_key=course, decision=body.decision, reason=body.reason.strip(), actor=x_actor or "admissions officer",
                     actor_role=x_role, idempotency_key=body.idempotency_key, result_id=r.get("result_id", ""),
                     subject_title=r["course"]["title"], qualification=r["course"].get("curriculum_qualification") or "",
                     target_code=t["code"] if t else None)
        db.add(d)
        db.commit()
        if body.decision in ("ACCEPTED", "DECLINED"):
            rt().precedent.add(Precedent(d.subject_title, d.qualification or "demo", d.target_code, body.decision, d.reason, d.actor, iso(d.at)))
        store.audit(db, app_id, d.actor, f"decision_{body.decision.lower()}", course)  # reason text stays in the decisions table
        return {"decision": d.decision, "reason": d.reason, "actor": d.actor, "actor_role": d.actor_role,
                "idempotency_key": d.idempotency_key, "at": iso(d.at)}


@router.get("/applications/{app_id}/audit")
def audit(app_id: str):
    with app_session() as db:
        get_app(db, app_id)
        rows = db.scalars(select(AuditEvent).where(AuditEvent.app_id == app_id).order_by(AuditEvent.id.desc()).limit(200))
        return [{"at": iso(a.at), "actor": a.actor, "event": a.event, "object": a.object, "details": a.details} for a in rows]


@router.post("/dev/reset")
def reset():
    if not get_settings().dev_console:
        raise HTTPException(404)
    with app_session() as db:
        if _active(db):
            raise HTTPException(409, "A job is queued or running")
        for model in (RowState, RowAction, ModelLog, TraceEvent, UsageEntry, JobUnit, Decision, AuditEvent, Job, Application):
            db.execute(delete(model))
        db.commit()
    reset_runtime()
    with app_session() as db:
        seed_demo(db)
    events.clear()
    return {"reset": True, "at": datetime.now(UTC).isoformat()}
