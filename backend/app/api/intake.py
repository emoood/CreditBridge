"""Upload (returns a job id at once), the verification table, curriculum linking, and the officer gate (confirm)."""
import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from ..config import get_settings
from ..db.models import Application, Job
from ..db.session import app_session
from ..services import student_record as sr
from ..services.curriculum import curriculum
from ..services.documents import UploadError, render_page_png, save_upload
from ..services.linking import autolink, candidates, subject_ref
from ..workers import store
from ..workers.queue import worker
from .common import DECIDER_ROLE, app_summary, get_app, short_id

router = APIRouter(prefix="/api/v1")
EDITABLE = ("code", "title", "credits", "grade", "term")


def _detail(db, app: Application) -> dict[str, Any]:
    return {**app_summary(db, app), "document": app.document and {k: v for k, v in app.document.items() if k != "path"},
            "extraction": app.extraction, "rows": app.rows, "corrections": app.corrections,
            "curriculum": {"available": curriculum.error is None, "error": curriculum.error}}


@router.get("/applications")
def list_applications():
    with app_session() as db:
        return [app_summary(db, a) for a in db.scalars(select(Application).order_by(Application.created_at.desc()))]


@router.get("/applications/{app_id}")
def get_application(app_id: str):
    with app_session() as db:
        return _detail(db, get_app(db, app_id))


@router.post("/applications")
async def create_application(file: UploadFile = File(...),  # noqa: B008
                             student_label: str = Form(""), student_id: str = Form(""), plant_misread: str = Form(""), program: str = Form(""), qualification: str = Form(""), case_number: str = Form(""),
                             x_role: str | None = Header(default=None)):
    """Store + hash the file, create the application and the reading job, return immediately. Reading happens in the worker."""
    data = await file.read()
    try:
        doc = save_upload(get_settings().document_root, file.filename or "upload", data, 15, 40)
    except UploadError as e:
        raise HTTPException(400, str(e)) from e
    rec = sr.load_record(case_number) if case_number.strip() else None
    if case_number.strip() and rec is None:
        raise HTTPException(404, f"No student record for case number {case_number.strip()}")
    if rec:  # the case number loads name, ID, contact, program, term and curriculum from the (demo) student data
        student_label, student_id, program, qualification = rec["name"], rec["student_id"], rec["program"], qualification or rec["curriculum"]
    with app_session() as db:
        app = Application(id=f"APP-{uuid.uuid4().hex[:8].upper()}", kind="upload", student=student_label.strip() or "Uploaded transcript",
                          program=program.strip(), status="READING", document=doc, qualification_hint=qualification, extraction={"student_id": student_id.strip(), **({"student_record": rec} if rec else {}), **({"planted_misread": {"subject": "AP Chemistry", "field": "mark", "wrong": 65}} if plant_misread.strip().lower() in ("1", "true", "on") and get_settings().demo_mode else {})})
        job = Job(id=uuid.uuid4().hex[:12], app_id=app.id, phase="READING", status="QUEUED")
        db.add_all([app, job])
        db.commit()
        store.audit(db, app.id, x_role or "unknown", "transcript_uploaded", job.id, sha256=doc["sha256"], pages=doc["page_count"], bytes=doc["size"])
        out = {**app_summary(db, app), "job_id": job.id}
    worker.enqueue(out["job_id"])
    return out


@router.get("/students/{case_number}")
def student_record(case_number: str):
    """The (demo) student record for a case number, so the upload form can show who this is before the file is read."""
    rec = sr.load_record(case_number)
    if rec is None:
        raise HTTPException(404, "No student record for this case number")
    return {**rec, "plan": sr.plan_for(rec["program"])}


class StudentPatch(BaseModel):
    name: str | None = None
    student_id: str | None = None


@router.patch("/applications/{app_id}/student")
def patch_student(app_id: str, body: StudentPatch):
    """Name and ID shown in the header (synthetic data only in this prototype)."""
    with app_session() as db:
        app = get_app(db, app_id)
        if body.name is not None:
            app.student = body.name.strip()[:160] or "Uploaded transcript"
        if body.student_id is not None:
            store.set_json(app, "extraction", {**(app.extraction or {}), "student_id": body.student_id.strip()[:40]})
        db.commit()
        return app_summary(db, app)


class RowPatch(BaseModel):
    code: str | None = None
    title: str | None = None
    credits: str | None = None
    grade: str | None = None
    term: str | None = None
    subject_id: str | None = None  # "" unlinks
    included: bool | None = None


def _editable(db, app_id: str) -> Application:
    app = get_app(db, app_id)
    if app.kind == "demo":
        raise HTTPException(400, "The demo application is read-only")
    if app.status != "EXTRACTION_REVIEW_REQUIRED":
        raise HTTPException(409, f"The table can only be edited during verification (status is {app.status})")
    return app


def _row(app: Application, row_id: str) -> dict[str, Any]:
    row = next((r for r in app.rows if r["id"] == row_id), None)
    if not row:
        raise HTTPException(404, "Unknown row")
    return row


@router.patch("/applications/{app_id}/rows/{row_id}")
def patch_row(app_id: str, row_id: str, body: RowPatch, x_actor: str | None = Header(default=None)):
    with app_session() as db:
        app = _editable(db, app_id)
        rows = [dict(r) for r in app.rows]
        row = next((r for r in rows if r["id"] == row_id), None)
        if not row:
            raise HTTPException(404, "Unknown row")
        ch = body.model_dump(exclude_none=True)
        if ch.get("credits", "").strip():
            try:
                Decimal(ch["credits"])
            except InvalidOperation as e:
                raise HTTPException(422, "Credits must be a number") from e
        for f in EDITABLE:
            if f in ch:
                store.audit(db, app_id, x_actor or "officer", "row_corrected", row_id, field=f)  # values are not logged
                row[f] = ch[f].strip()
                row["uncertain_fields"] = [u for u in row.get("uncertain_fields", []) if u != f]
                row["corrected"] = True
        if "included" in ch:
            row["included"] = ch["included"]
        if "subject_id" in ch:
            if ch["subject_id"] == "":
                row["subject"], row["curriculum_status"] = None, "NEEDS_SYLLABUS"
            else:
                s = next((x for x in curriculum.subjects() if x.id == ch["subject_id"]), None)
                if not s:
                    raise HTTPException(404, "Unknown curriculum subject")
                row["subject"], row["curriculum_status"] = subject_ref(s.__dict__), "LINKED"
            row["auto_linked"] = False
            row["corrected"] = True
            store.audit(db, app_id, x_actor or "officer", "subject_linked", row_id, linked=bool(row["subject"]))
        if {"title", "code"} & ch.keys():
            row["candidates"] = candidates(row, None)
        store.set_json(app, "rows", rows)
        db.commit()
        return row


@router.post("/applications/{app_id}/rows")
def add_row(app_id: str):
    with app_session() as db:
        app = _editable(db, app_id)
        row = {"id": uuid.uuid4().hex[:8], "code": "", "title": "", "title_ar": "", "credits": "", "grade": "", "term": "", "page": 1,
               "evidence_ref": "", "uncertain_fields": [], "flags": [], "subject": None, "candidates": [], "included": True,
               "corrected": True, "curriculum_status": "NEEDS_SYLLABUS"}
        store.set_json(app, "rows", [*app.rows, row])
        db.commit()
        return row


@router.delete("/applications/{app_id}/rows/{row_id}")
def delete_row(app_id: str, row_id: str):
    with app_session() as db:
        app = _editable(db, app_id)
        _row(app, row_id)
        store.set_json(app, "rows", [r for r in app.rows if r["id"] != row_id])
        db.commit()
        return {"deleted": True}


class LinkIn(BaseModel):
    qualification: str | None = None
    relink: bool = False


@router.post("/applications/{app_id}/link")
async def link_subjects(app_id: str, body: LinkIn):
    await asyncio.to_thread(curriculum.subjects)
    with app_session() as db:
        app = _editable(db, app_id)
        rows = [dict(r) for r in app.rows]
        for row in rows:
            if body.relink and row.get("auto_linked"):
                row["subject"] = None
            autolink(row, body.qualification)
        store.set_json(app, "rows", rows)
        db.commit()
        return _detail(db, app)


@router.get("/applications/{app_id}/preview/{page}")
def preview(app_id: str, page: int):
    with app_session() as db:
        app = get_app(db, app_id)
        if not app.document:
            raise HTTPException(404, "No document")
        path = app.document["path"]
    try:
        return Response(render_page_png(path, page), media_type="image/png")
    except UploadError as e:
        raise HTTPException(404, str(e)) from e


def _corrections(extracted: list[dict], current: list[dict]) -> list[dict[str, Any]]:
    """Field-level differences the officer made. Values are structured fields (subject, grade...), not document text."""
    before = {r["id"]: r for r in extracted}
    out: list[dict[str, Any]] = []
    for r in current:
        b = before.get(r["id"])
        if b is None:
            out.append({"row": r["id"], "change": "added"})
            continue
        for f in (*EDITABLE, "included"):
            if b.get(f) != r.get(f):
                out.append({"row": r["id"], "field": f, "from": b.get(f), "to": r.get(f)})
        if (b.get("subject") or {}).get("id") != (r.get("subject") or {}).get("id"):
            out.append({"row": r["id"], "field": "curriculum_subject", "from": (b.get("subject") or {}).get("name"),
                        "to": (r.get("subject") or {}).get("name")})
    out += [{"row": rid, "change": "removed"} for rid in before if rid not in {r["id"] for r in current}]
    return out


class ConfirmIn(BaseModel):
    officer: str | None = None


@router.post("/applications/{app_id}/confirm")
def confirm(app_id: str, body: ConfirmIn | None = None, x_role: str | None = Header(default=None), x_actor: str | None = Header(default=None)):
    """The officer gate. Records who, when and what was corrected, then queues Phase 2 exactly once (replay returns the same job)."""
    if x_role != DECIDER_ROLE:
        raise HTTPException(403, "Only an admissions officer can confirm an extraction")
    officer = (body.officer if body and body.officer else None) or x_actor or "admissions officer"
    with app_session() as db:
        app = db.scalar(select(Application).where(Application.id == app_id).with_for_update())  # serialises double clicks / replays
        if app is None:
            raise HTTPException(404, "Unknown application")
        if app.confirmed_at is not None:
            return {"job_id": app.evaluation_job_id, "already_confirmed": True, "confirmed_by": app.confirmed_by}
        if app.kind != "upload" or app.status != "EXTRACTION_REVIEW_REQUIRED":
            raise HTTPException(409, f"Nothing to confirm (status is {app.status})")
        included = [r for r in app.rows if r.get("included")]
        if not included:
            raise HTTPException(400, "There are no subjects to evaluate")
        rows = [dict(r) for r in app.rows]
        n = 0
        for r in rows:
            if r.get("included"):
                n += 1
                r["source_id"] = f"{short_id(app.id)}-{n:02d}"
        job = Job(id=uuid.uuid4().hex[:12], app_id=app.id, phase="EVALUATION", status="QUEUED")
        db.add(job)
        store.set_json(app, "rows", rows)
        store.set_json(app, "corrections", _corrections(app.extracted_rows, rows))
        app.confirmed_by, app.confirmed_at, app.status, app.evaluation_job_id = officer, datetime.now(UTC), "CONFIRMED", job.id
        db.commit()
        store.audit(db, app.id, officer, "extraction_confirmed", job.id, subjects=len(included), corrections=len(app.corrections))
        job_id = job.id
    worker.enqueue(job_id)
    return {"job_id": job_id, "already_confirmed": False, "confirmed_by": officer}


@router.get("/curriculum/qualifications")
async def qualifications():
    await asyncio.to_thread(curriculum.subjects)
    return {"qualifications": curriculum.qualifications(), "available": curriculum.error is None, "error": curriculum.error}


@router.get("/curriculum/subjects")
async def subjects(q: str = "", qualification: str | None = None, limit: int = 20):
    await asyncio.to_thread(curriculum.subjects)
    return curriculum.search(q, qualification, min(limit, 50))
