"""CreditBridge main-app tables (schema `public`). Portable SQLAlchemy 2 so tests can run on SQLite.
Do NOT define curriculum tables here: the `curriculum` schema belongs to the teammate's module."""
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(UTC)


def _id() -> uuid.UUID:
    return uuid.uuid4()


class Base(DeclarativeBase):
    pass


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # e.g. aud-undergrad-demo-2026-10-22
    source_url: Mapped[str] = mapped_column(Text)
    effective_date: Mapped[str | None] = mapped_column(String(20))
    config: Mapped[dict] = mapped_column(JSON)
    verified_flag: Mapped[bool] = mapped_column(Boolean, default=False)  # staff verification, False for the demo


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    synthetic_student_id: Mapped[str] = mapped_column(String(40))
    student_label: Mapped[str] = mapped_column(String(120), default="")
    program: Mapped[str] = mapped_column(String(120), default="")
    policy_version_id: Mapped[str] = mapped_column(ForeignKey("policy_versions.id"))
    catalog_version: Mapped[str] = mapped_column(String(80), default="")
    status: Mapped[str] = mapped_column(String(40), default="DRAFT", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    courses: Mapped[list["SourceCourse"]] = relationship(back_populates="application")


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    application_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("applications.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # TRANSCRIPT | SOURCE_SYLLABUS | TARGET_SYLLABUS | CATALOG | POLICY
    original_filename: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_path: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(24))  # UPLOADED | OFFICIAL_PUBLIC_URL | CURATED_DEMO
    source_url: Mapped[str | None] = mapped_column(Text)
    institution: Mapped[str | None] = mapped_column(String(160))
    academic_year: Mapped[str | None] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer, default=1)
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class DocumentExcerpt(Base):
    """Every citation must resolve to a row here."""
    __tablename__ = "document_excerpts"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)  # human-readable id used in citations
    document_id: Mapped[str] = mapped_column(String(120), index=True)  # doc uuid or fixture doc id
    page: Mapped[int] = mapped_column(Integer, default=1)
    text: Mapped[str] = mapped_column(Text)
    excerpt_hash: Mapped[str] = mapped_column(String(64))


class SourceCourse(Base):
    __tablename__ = "source_courses"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    code: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(200), default="")
    title_ar: Mapped[str] = mapped_column(String(200), default="")
    credits: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    grade: Mapped[str | None] = mapped_column(String(8))
    term: Mapped[str] = mapped_column(String(40), default="")
    evidence_ref: Mapped[str] = mapped_column(String(120), default="")
    uncertain_fields: Mapped[list] = mapped_column(JSON, default=list)
    syllabus_document_id: Mapped[str | None] = mapped_column(String(120))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(40), default="NEEDS_EXTRACTION_CONFIRMATION", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)  # optimistic token
    application: Mapped[Application] = relationship(back_populates="courses")


class SourceCourseCorrection(Base):
    __tablename__ = "source_course_corrections"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    source_course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_courses.id"), index=True)
    field: Mapped[str] = mapped_column(String(40))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(80))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TargetCourse(Base):
    __tablename__ = "target_courses"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    catalog_version: Mapped[str] = mapped_column(String(80), index=True)
    code: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(200))
    level: Mapped[int | None] = mapped_column(Integer)
    credits: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    description: Mapped[str] = mapped_column(Text, default="")
    outcomes: Mapped[list] = mapped_column(JSON, default=list)  # [{id, text}]
    prerequisites: Mapped[str | None] = mapped_column(Text)
    restricted_type: Mapped[str | None] = mapped_column(String(30))
    type_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source_document_id: Mapped[str | None] = mapped_column(String(120))
    source_page: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (Index("ix_target_catalog_code", "catalog_version", "code", unique=True),)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.id"), index=True)
    stage: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")  # QUEUED|RUNNING|DONE|FAILED|INTERRUPTED
    progress: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MatchRun(Base):
    __tablename__ = "match_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_runs.id"))
    source_course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_courses.id"), index=True)
    status: Mapped[str] = mapped_column(String(30))  # matcher result status
    target_course_code: Mapped[str | None] = mapped_column(String(30))
    provider: Mapped[str] = mapped_column(String(60), default="")
    model: Mapped[str] = mapped_column(String(60), default="")
    prompt_version: Mapped[str] = mapped_column(String(30), default="")
    retriever_mode: Mapped[str] = mapped_column(String(20), default="lexical")
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    output: Mapped[dict | None] = mapped_column(JSON)  # validated MatchOutput
    is_replay: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    version: Mapped[int] = mapped_column(Integer, default=1)  # optimistic token for decisions
    candidates: Mapped[list["MatchCandidate"]] = relationship(back_populates="run")


class MatchCandidate(Base):
    __tablename__ = "match_candidates"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    match_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("match_runs.id"), index=True)
    target_course_code: Mapped[str] = mapped_column(String(30))
    retrieval_score: Mapped[float] = mapped_column(Numeric(7, 4))  # relevance only, not equivalence
    rank: Mapped[int] = mapped_column(Integer)
    run: Mapped[MatchRun] = relationship(back_populates="candidates")


class MatchEvidence(Base):
    __tablename__ = "match_evidence"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    match_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("match_runs.id"), index=True)
    source_excerpt_id: Mapped[str] = mapped_column(ForeignKey("document_excerpts.id"))
    target_excerpt_id: Mapped[str] = mapped_column(ForeignKey("document_excerpts.id"))
    explanation: Mapped[str] = mapped_column(Text, default="")


class RuleResultRow(Base):
    __tablename__ = "rule_results"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    match_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("match_runs.id"), index=True)
    rule_id: Mapped[str] = mapped_column(String(60))
    policy_version_id: Mapped[str] = mapped_column(ForeignKey("policy_versions.id"))
    status: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class AgentReview(Base):
    __tablename__ = "agent_reviews"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    match_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("match_runs.id"), index=True)
    verdict: Mapped[str] = mapped_column(String(40))
    objections: Mapped[list] = mapped_column(JSON, default=list)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(60), default="")
    model: Mapped[str] = mapped_column(String(60), default="")
    prompt_version: Mapped[str] = mapped_column(String(30), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class HumanDecision(Base):
    """Append-only. The only place approval can exist."""
    __tablename__ = "human_decisions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    match_run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("match_runs.id"), index=True)
    decision: Mapped[str] = mapped_column(String(30))  # APPROVED | REJECTED | MORE_INFORMATION_REQUESTED
    reason: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(80))
    actor_role: Mapped[str] = mapped_column(String(30))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UploadRequest(Base):
    __tablename__ = "upload_requests"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    source_course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_courses.id"), index=True)
    missing_fields: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="DRAFT_IN_APP")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditEvent(Base):
    """Append-only."""
    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    application_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("applications.id"), index=True)
    actor: Mapped[str] = mapped_column(String(80))
    event: Mapped[str] = mapped_column(String(60))
    object_type: Mapped[str] = mapped_column(String(40), default="")
    object_id: Mapped[str] = mapped_column(String(80), default="")
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class LlmEvent(Base):
    """Dev console history (optional persistence of the in-memory event stream)."""
    __tablename__ = "llm_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_id)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
