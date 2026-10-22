"""Small DB helpers for jobs, units and audit. Every state change is committed immediately so a crash loses at most the
unit that was running."""
import copy
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import Application, AuditEvent, Job, JobUnit


def now() -> datetime:
    return datetime.now(UTC)


def audit(db: Session, app_id: str | None, actor: str, event: str, obj: str = "", **details: Any) -> None:
    """Append-only. details must be ids, counts, statuses: never raw document text."""
    db.add(AuditEvent(app_id=app_id, actor=actor, event=event, object=obj, details=details))
    db.commit()


def set_json(obj: Any, attr: str, value: Any) -> None:
    setattr(obj, attr, copy.deepcopy(value))  # reassign a copy so SQLAlchemy sees the change


def get_unit(db: Session, app_id: str, stage: str, key: str) -> JobUnit | None:
    return db.scalar(select(JobUnit).where(JobUnit.app_id == app_id, JobUnit.stage == stage, JobUnit.unit_key == key))


def plan_units(db: Session, job: Job, stage: str, keys: list[str], reset: bool) -> list[JobUnit]:
    """Create (or, when starting fresh, reset) one unit per key. On resume nothing finished is touched."""
    units = []
    for seq, key in enumerate(keys):
        u = get_unit(db, job.app_id, stage, key)
        if u is None:
            u = JobUnit(app_id=job.app_id, job_id=job.id, stage=stage, unit_key=key, seq=seq)
            db.add(u)
        elif reset:
            u.job_id, u.seq, u.status, u.attempts, u.error_code, u.result = job.id, seq, "PENDING", 0, "", None
            u.started_at = u.finished_at = None
            u.duration_s = 0.0
        else:
            u.job_id, u.seq = job.id, seq
        units.append(u)
    db.commit()
    return units


def start_unit(db: Session, job: Job, u: JobUnit, method: str = "") -> None:
    u.status, u.started_at, u.method, u.error_code = "RUNNING", now(), method, ""
    u.attempts += 1
    job.stage, job.current_unit = u.stage, u.unit_key
    db.commit()


def finish_unit(db: Session, u: JobUnit, status: str, result: dict | None = None, error: str = "", method: str | None = None) -> None:
    u.status, u.error_code, u.finished_at = status, error, now()
    started = u.started_at if (u.started_at and u.started_at.tzinfo) else (u.started_at.replace(tzinfo=UTC) if u.started_at else None)  # SQLite drops the zone
    u.duration_s = round((u.finished_at - started).total_seconds(), 2) if started else 0.0
    if method is not None:
        u.method = method
    if result is not None:
        set_json(u, "result", result)
    db.commit()


def units_of(db: Session, job_id: str) -> list[JobUnit]:
    return list(db.scalars(select(JobUnit).where(JobUnit.job_id == job_id).order_by(JobUnit.id)))


def get_app(db: Session, app_id: str) -> Application | None:
    return db.get(Application, app_id)
