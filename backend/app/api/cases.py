"""Row-level actions of the Case-agent path: include a course, supply evidence, read the activity trace and usage ledger."""
import json
import uuid
from typing import Any

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from sqlalchemy import select

from ..config import get_settings
from ..db.models import Job
from ..db.session import app_session
from ..services import mapping as mp
from ..services import usage
from ..services.documents import UploadError, save_upload
from ..workers import store
from ..workers.queue import worker
from .common import DECIDER_ROLE, get_app

router = APIRouter(prefix="/api/v1")


def _row_job(db, app, row_id: str) -> Job:
    if any(j.status in ("QUEUED", "RUNNING") for j in db.scalars(select(Job).where(Job.app_id == app.id))):
        raise HTTPException(409, "This application is still being worked on; try again in a moment")
    job = Job(id=uuid.uuid4().hex[:12], app_id=app.id, phase="CASE", status="QUEUED", summary={"row_id": row_id})
    db.add(job)
    db.commit()
    return job


def _need_row(db, app_id: str, row_id: str):
    app = get_app(db, app_id)
    if app.kind != "upload" or app.confirmed_at is None:
        raise HTTPException(409, "The extraction has not been confirmed yet")
    if not any(r["id"] == row_id for r in app.rows):
        raise HTTPException(404, "Unknown row")
    st = mp.get_state(db, app_id, row_id)
    if st is None:
        raise HTTPException(409, "This row has not been evaluated yet")
    return app, st


@router.post("/applications/{app_id}/rows/{row_id}/include")
def include_row(app_id: str, row_id: str, x_role: str | None = Header(default=None), x_actor: str | None = Header(default=None)):
    """One-click include of a 'Not evaluated' row: it goes through the lookup and, if needed, the Case agent."""
    if x_role != DECIDER_ROLE:
        raise HTTPException(403, "Only an admissions officer can include a course")
    with app_session() as db:
        app, st = _need_row(db, app_id, row_id)
        if st.status != mp.NOT_EVAL or not st.can_include:
            raise HTTPException(409, "This course cannot be included")
        mp.save_state(db, app_id, row_id, included_override=True)
        job = _row_job(db, app, row_id)
        store.audit(db, app_id, x_actor or "officer", "row_included", row_id)
        job_id = job.id
    worker.enqueue(job_id)
    return {"job_id": job_id}


@router.post("/applications/{app_id}/rows/{row_id}/evidence")
async def add_evidence(app_id: str, row_id: str, facts: str = Form("{}"), note: str = Form(""), file: UploadFile | None = File(None),  # noqa: B008
                       x_role: str | None = Header(default=None), x_actor: str | None = Header(default=None)):
    """The officer hands a "Needs your input" row back with evidence: facts (e.g. {"exam_score": 5}), a typed note ("this is AP Physics C Mechanics"),
    and/or a document. Open requests are marked received and ONLY this row is worked again."""
    if x_role != DECIDER_ROLE:
        raise HTTPException(403, "Only an admissions officer can add evidence")
    try:
        new: dict[str, Any] = json.loads(facts or "{}")
        if not isinstance(new, dict):
            raise TypeError
    except (ValueError, TypeError) as e:
        raise HTTPException(422, "facts must be a JSON object") from e
    doc = None
    if file is not None and file.filename:
        try:
            doc = save_upload(get_settings().document_root, file.filename, await file.read(), 15, 40)
        except UploadError as e:
            raise HTTPException(400, str(e)) from e
    with app_session() as db:
        app, st = _need_row(db, app_id, row_id)
        if not new and not doc and not note.strip():
            raise HTTPException(422, "Provide a note, a fact or a document")
        notes = [*((st.facts or {}).get("_notes", [])), *([{"at": store.now().isoformat(), "by": x_actor or "officer", "text": note.strip()[:600], "document": bool(doc)}] if note.strip() else [])]
        reqs = [{**r, "status": "RECEIVED", **({"document": {"filename": doc["filename"], "sha256": doc["sha256"]}} if doc else {})} if r["status"] in ("DRAFT", "SENT") else r
                for r in (st.requests or [])]
        mp.save_state(db, app_id, row_id, facts={**(st.facts or {}), **new, **({"_notes": notes} if notes else {})}, requests=reqs, status=mp.CONDITIONAL, reason="Evidence received; re-checking")
        job = _row_job(db, app, row_id)
        store.audit(db, app_id, x_actor or "officer", "evidence_added", row_id, facts=sorted(new), document=bool(doc), note=bool(note.strip()))  # values are not logged
        usage.trace(app_id, row_id, "code", "note_or_evidence_received", {"facts": sorted(new), "document": bool(doc), "note": bool(note.strip())})
        job_id = job.id
    worker.enqueue(job_id)
    return {"job_id": job_id}


@router.get("/applications/{app_id}/activity")
def activity(app_id: str):
    with app_session() as db:
        get_app(db, app_id)
    return usage.activity(app_id)
