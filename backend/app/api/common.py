"""Shared API helpers: application summaries, job serialization, seeding the demo application."""
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import Application, Decision, Job, JobUnit, iso
from ..devlog import events
from ..runtime import rt

DEMO_ID = "DEMO-001"
DECIDER_ROLE = "admissions"


def short_id(app_id: str) -> str:
    return app_id.replace("-", "")[-6:].upper()


def seed_demo(db: Session) -> None:
    if db.get(Application, DEMO_ID):
        return
    rows = [{"id": c.code, "source_id": c.code, "code": c.code, "title": c.title, "title_ar": c.title_ar, "credits": c.credits,
             "grade": c.grade, "term": c.term, "page": 1, "evidence_ref": "", "uncertain_fields": [], "flags": [], "subject": None,
             "candidates": [], "included": True, "corrected": False, "curriculum_status": "FIXTURE"} for c in rt().repo.sources.values()]
    db.add(Application(id=DEMO_ID, kind="demo", student="Sample Student (synthetic)", program="BSc Computer Science (demo)",
                       institution="Gulf Example University (synthetic)", status="READY_FOR_EVALUATION", rows=rows, extracted_rows=rows,
                       extraction={"method": "fixtures"}))
    db.commit()


def get_app(db: Session, app_id: str) -> Application:
    app = db.get(Application, app_id)
    if not app:
        raise HTTPException(404, "Unknown application")
    return app


def decided_count(db: Session, app_id: str) -> int:
    return db.scalar(select(func.count(func.distinct(Decision.unit_key))).where(Decision.app_id == app_id)) or 0


def app_status(db: Session, app: Application) -> str:
    n_results = db.scalar(select(func.count()).select_from(JobUnit).where(JobUnit.app_id == app.id, JobUnit.stage == "evaluate",
                                                                          JobUnit.status == "DONE")) or 0
    if app.status in ("REVIEW_READY", "EVALUATION_PARTIAL") and n_results and decided_count(db, app.id) >= n_results:
        return "DECISIONS_RECORDED"
    return app.status


def app_summary(db: Session, app: Application) -> dict[str, Any]:
    reading = db.scalar(select(Job).where(Job.app_id == app.id, Job.phase == "READING").order_by(Job.created_at.desc()))
    return {"id": app.id, "kind": app.kind, "student": app.student, "program": app.program, "institution": app.institution,
            "courses": sum(1 for r in app.rows if r.get("included")), "status": app_status(db, app), "decided": decided_count(db, app.id),
            "catalog": rt().repo.catalog_label, "created_at": iso(app.created_at), "confirmed": app.confirmed_at is not None,
            "reading_job_id": reading.id if reading else None, "evaluation_job_id": app.evaluation_job_id,
            "replay": (app.extraction or {}).get("replay"), "student_id": (app.extraction or {}).get("student_id", "")}


def _utc(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _secs(a: datetime | None, b: datetime | None) -> float | None:
    if not a:
        return None
    return round((_utc(b or datetime.now(UTC)) - _utc(a)).total_seconds(), 1)


def job_dict(db: Session, job: Job) -> dict[str, Any]:
    units = list(db.scalars(select(JobUnit).where(JobUnit.job_id == job.id).order_by(JobUnit.id)))
    per_stage: dict[str, float] = {}
    for u in units:
        per_stage[u.stage] = round(per_stage.get(u.stage, 0.0) + (u.duration_s or 0.0), 1)
    started = _utc(job.started_at).timestamp() if job.started_at else None
    activity = [{"ts": e["ts"], "kind": e["kind"], "course": e.get("course"), "tool": e.get("tool"), "stage": e.get("stage"),
                 "role": e.get("role"), "ok": e.get("ok"), "duration_s": e.get("duration_s")}
                for e in events.recent(400) if started and e["ts"] >= started and e["kind"] in ("tool_call", "stage", "llm_call", "error")]
    s = get_settings()
    return {"id": job.id, "app_id": job.app_id, "phase": job.phase, "status": job.status, "stage": job.stage, "current_unit": job.current_unit,
            "flags": job.flags, "summary": job.summary, "route": job.route or s.llm_default_route,
            "model": job.model or s.route(s.llm_default_route)[1],
            "timings": {"created_at": iso(job.created_at), "started_at": iso(job.started_at),
                        "finished_at": iso(job.finished_at), "total_secs": _secs(job.started_at, job.finished_at),
                        "per_stage_secs": per_stage},
            "units": [{"stage": u.stage, "key": u.unit_key, "status": u.status, "attempts": u.attempts, "method": u.method,
                       "error_code": u.error_code, "duration_s": u.duration_s} for u in units],
            # compatibility with the existing UI
            "started_at": started, "finished_at": _utc(job.finished_at).timestamp() if job.finished_at else None,
            "courses": {u.unit_key: u.status.replace("PENDING", "QUEUED") for u in units if u.stage in ("evaluate", "read")},
            "errors": {u.unit_key: u.error_code for u in units if u.status == "FAILED_RETRYABLE"},
            "llm_calls": sum(1 for a in activity if a["kind"] == "llm_call"), "activity": activity[-25:]}
