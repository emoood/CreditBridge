"""This app's own tables (local PostgreSQL). No curriculum tables here: that schema belongs to the teammate's module.
Portable SQLAlchemy so unit tests can run on SQLite. Raw document text is never stored: only structured fields."""
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None) -> str | None:
    """Always an explicit-UTC ISO string, whether or not the database kept the timezone."""
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC).isoformat()


class Base(DeclarativeBase):
    pass


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # demo | upload
    student: Mapped[str] = mapped_column(String(160), default="")
    program: Mapped[str] = mapped_column(String(160), default="")
    institution: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(40), index=True)
    document: Mapped[dict | None] = mapped_column(JSON)  # id, path, sha256, filename, pages (no text)
    extraction: Mapped[dict] = mapped_column(JSON, default=dict)  # method, flags, checks, block summary
    rows: Mapped[list[Any]] = mapped_column(JSON, default=list)  # the verification table
    extracted_rows: Mapped[list[Any]] = mapped_column(JSON, default=list)  # snapshot at extraction, to compute corrections
    qualification_hint: Mapped[str] = mapped_column(String(80), default="")
    confirmed_by: Mapped[str | None] = mapped_column(String(80))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    corrections: Mapped[list[Any]] = mapped_column(JSON, default=list)
    evaluation_job_id: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    app_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    phase: Mapped[str] = mapped_column(String(12))  # READING | EVALUATION
    status: Mapped[str] = mapped_column(String(14), index=True)  # QUEUED | RUNNING | DONE | FAILED | INTERRUPTED
    stage: Mapped[str] = mapped_column(String(30), default="")
    current_unit: Mapped[str] = mapped_column(String(60), default="")
    route: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(80), default="")
    flags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobUnit(Base):
    """One row per stage and unit of work (a page block in READING, a subject in EVALUATION). Unique per application, so
    re-running a unit overwrites only its own row."""
    __tablename__ = "job_units"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(ForeignKey("applications.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(30))
    unit_key: Mapped[str] = mapped_column(String(60))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(18), default="PENDING")  # PENDING RUNNING DONE FAILED_RETRYABLE SKIPPED
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    method: Mapped[str] = mapped_column(String(30), default="")
    error_code: Mapped[str] = mapped_column(String(40), default="")
    duration_s: Mapped[float] = mapped_column(Float, default=0.0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict | None] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("app_id", "stage", "unit_key", name="uq_unit"), Index("ix_unit_job", "job_id", "seq"))


class Decision(Base):
    """Human decisions. Append-only; the pipeline never writes, changes or deletes a row here."""
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    unit_key: Mapped[str] = mapped_column(String(60))
    decision: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80))
    actor_role: Mapped[str] = mapped_column(String(30))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    result_id: Mapped[str] = mapped_column(String(40), default="")  # which suggestion this decision answered
    subject_title: Mapped[str] = mapped_column(String(200), default="")
    qualification: Mapped[str] = mapped_column(String(80), default="")
    target_code: Mapped[str | None] = mapped_column(String(40))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditEvent(Base):
    """Append-only. `details` holds ids, counts and statuses only: never raw document text."""
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str | None] = mapped_column(String(40), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    actor: Mapped[str] = mapped_column(String(80))
    event: Mapped[str] = mapped_column(String(60))
    object: Mapped[str] = mapped_column(String(80), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class CourseMapping(Base):
    """Dummy mapping table (course -> AUD equivalent), seeded from policies/dummy_mappings.yaml. The real table replaces it later;
    the lookup code only reads this shape. A condition (min grade, exam variant, exam score, ...) is optional."""
    __tablename__ = "course_mappings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    qualification: Mapped[str] = mapped_column(String(80), default="")
    subject_code: Mapped[str] = mapped_column(String(60), index=True)  # curriculum database subject code, e.g. AP-CALC-AB
    subject_name: Mapped[str] = mapped_column(String(160), default="")
    aud_code: Mapped[str] = mapped_column(String(40))
    aud_credits: Mapped[int] = mapped_column(Integer, default=3)
    condition: Mapped[dict | None] = mapped_column(JSON)  # {"type": "min_grade|min_exam_score|exam_variant", "value": ..., "evidence": "..."}
    version: Mapped[str] = mapped_column(String(30), default="")
    status: Mapped[str] = mapped_column(String(10), default="ACTIVE")  # ACTIVE | STALE | DRAFT
    note: Mapped[str] = mapped_column(Text, default="")
    programs: Mapped[list | None] = mapped_column(JSON)  # None = applies to every program; else only to the listed programs (which then override the general entry)


class RowState(Base):
    """One row's working state: its status in the officer vocabulary, why, pending requests and the facts collected so far.
    Persisted so work can pause (waiting on a document) and resume for just this row."""
    __tablename__ = "row_states"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    row_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(24))  # Mapped | Conditional | No mapping | Waiting on document | Not evaluated
    kind: Mapped[str] = mapped_column(String(24), default="")  # not_relevant | mapped | unmapped | stale | condition_unmet | condition_missing | rule_blocked | ...
    reason: Mapped[str] = mapped_column(Text, default="")
    can_include: Mapped[bool] = mapped_column(default=False)
    included_override: Mapped[bool] = mapped_column(default=False)  # the officer's one-click include
    mapping: Mapped[dict | None] = mapped_column(JSON)  # snapshot of the mapping used (id, version, aud_code, condition)
    condition_result: Mapped[dict | None] = mapped_column(JSON)
    finding: Mapped[dict | None] = mapped_column(JSON)  # the Case agent's finding for the officer
    requests: Mapped[list[Any]] = mapped_column(JSON, default=list)  # drafted requests; nothing is sent
    facts: Mapped[dict] = mapped_column(JSON, default=dict)  # evidence entered by the officer, e.g. {"exam_score": 5}
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("app_id", "row_id", name="uq_row_state"),)


class TraceEvent(Base):
    """Every action by the Case agent (and the code steps around it), for the activity drawer. No raw transcript text."""
    __tablename__ = "trace_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(String(40), index=True)
    row_id: Mapped[str] = mapped_column(String(40), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    actor: Mapped[str] = mapped_column(String(20))  # case_agent | code
    name: Mapped[str] = mapped_column(String(60))
    args: Mapped[dict] = mapped_column(JSON, default=dict)
    ok: Mapped[bool] = mapped_column(default=True)
    detail: Mapped[str] = mapped_column(Text, default="")


class UsageEntry(Base):
    """Usage ledger: one row per model call."""
    __tablename__ = "usage_ledger"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(String(40), index=True)
    row_id: Mapped[str] = mapped_column(String(40), default="")
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    stage: Mapped[str] = mapped_column(String(30), default="")
    provider: Mapped[str] = mapped_column(String(40), default="")
    model: Mapped[str] = mapped_column(String(80), default="")
    seconds: Mapped[float] = mapped_column(Float, default=0.0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)  # local models cost nothing; a hosted price table goes here later


class RowAction(Base):
    """Every officer action on a row (confirm, change, reject, no credit, send for review, batch confirm, correction) with who, when
    and the ORIGINAL suggestion, so an audit can always show what the system proposed and what the officer did. Append-only."""
    __tablename__ = "row_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    row_id: Mapped[str] = mapped_column(String(40), default="")  # "" for application-level actions (APPROVE)
    action: Mapped[str] = mapped_column(String(20))  # CONFIRM CHANGE REJECT NO_CREDIT SEND_FOR_REVIEW CORRECT APPROVE
    actor: Mapped[str] = mapped_column(String(80))
    actor_role: Mapped[str] = mapped_column(String(30))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    original: Mapped[dict] = mapped_column(JSON, default=dict)  # the system's suggestion at that moment: aud_code, credits, status, mapping version
    chosen: Mapped[dict] = mapped_column(JSON, default=dict)  # what the officer settled on: aud_code, credits (None = no credit)
    reason: Mapped[str] = mapped_column(Text, default="")
    batch: Mapped[bool] = mapped_column(default=False)  # created by "Confirm all mapped"


class ModelLog(Base):
    """The model's raw reply to every call of an application, kept ONLY in demo mode (synthetic data) so a finished real run can be saved as a prepared run.
    Keyed by what the call was (stage, run key, index), not by when it happened."""
    __tablename__ = "model_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(String(40), index=True)
    stage: Mapped[str] = mapped_column(String(30))
    runkey: Mapped[str] = mapped_column(String(20))
    idx: Mapped[int] = mapped_column(Integer, default=0)
    response: Mapped[dict] = mapped_column(JSON, default=dict)


def __getattr__(name: str):
    if name == "OutboxMessage":
        from ..services.outbox import OutboxMessage
        return OutboxMessage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
