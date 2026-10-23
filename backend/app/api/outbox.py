"""API endpoints for Outbox: requests management, email preview, sending, and student replies."""
from typing import Any

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field

from ..db.models import Application
from ..db.session import app_session
from ..services import outbox as ob
from ..services.student_record import load_record
from .common import DECIDER_ROLE, get_app

router = APIRouter(prefix="/api/v1")


class AddRequestPayload(BaseModel):
    what: str = Field(min_length=2, max_length=400)
    why: str = Field(default="", max_length=400)
    type: str | None = Field(default=None)
    to: str = Field(default="student")


class EditRequestPayload(BaseModel):
    what: str | None = None
    why: str | None = None
    type: str | None = None


class SendEmailPayload(BaseModel):
    recipient: str | None = None
    upload_base_url: str = ""


@router.get("/applications/{app_id}/requests")
def list_student_requests(app_id: str):
    """List all open and past request items for this student across all courses."""
    with app_session() as db:
        get_app(db, app_id)
        items = ob.get_student_requests(db, app_id)
    return {
        "items": items,
        "templates": ob.TEMPLATES,
        "request_types": ob.REQUEST_TYPES,
    }


@router.post("/applications/{app_id}/rows/{row_id}/requests")
def add_row_request(
    app_id: str,
    row_id: str,
    payload: AddRequestPayload,
    x_role: str | None = Header(default=None),
):
    """Officer adds a request item to a specific row."""
    if x_role != DECIDER_ROLE:
        raise HTTPException(403, "Only an admissions officer can manage requests")
    with app_session() as db:
        get_app(db, app_id)
        try:
            item = ob.add_request_item(
                db,
                app_id,
                row_id,
                what=payload.what,
                why=payload.why,
                req_type=payload.type,
                to=payload.to,
            )
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
    return item


@router.delete("/applications/{app_id}/rows/{row_id}/requests/{request_id}")
def remove_row_request(
    app_id: str,
    row_id: str,
    request_id: str,
    x_role: str | None = Header(default=None),
):
    """Officer removes a request item."""
    if x_role != DECIDER_ROLE:
        raise HTTPException(403, "Only an admissions officer can manage requests")
    with app_session() as db:
        get_app(db, app_id)
        ok = ob.remove_request_item(db, app_id, row_id, request_id)
        if not ok:
            raise HTTPException(404, "Request item not found")
    return {"deleted": True, "request_id": request_id}


@router.patch("/applications/{app_id}/rows/{row_id}/requests/{request_id}")
def edit_row_request(
    app_id: str,
    row_id: str,
    request_id: str,
    payload: EditRequestPayload,
    x_role: str | None = Header(default=None),
):
    """Officer edits a request item's text or type."""
    if x_role != DECIDER_ROLE:
        raise HTTPException(403, "Only an admissions officer can manage requests")
    with app_session() as db:
        get_app(db, app_id)
        item = ob.edit_request_item(
            db,
            app_id,
            row_id,
            request_id,
            what=payload.what,
            why=payload.why,
            req_type=payload.type,
        )
        if not item:
            raise HTTPException(404, "Request item not found")
    return item


@router.get("/applications/{app_id}/outbox/preview")
def preview_email(app_id: str, upload_base_url: str = ""):
    """Preview the grouped email for this student built from all open request items."""
    with app_session() as db:
        app = get_app(db, app_id)
        rec = load_record(getattr(app, "case_number", "") or "") if getattr(app, "case_number", "") else None
        items = ob.get_student_requests(db, app_id)
        open_items = [i for i in items if i.get("status") in ("DRAFT", "SENT")]
        content = ob.build_email_content(app, rec, open_items, upload_base_url=upload_base_url)
        email_addr = (rec or {}).get("email") or ""
        import os
        sending_enabled = os.getenv("EMAIL_SENDING", "false").lower() in ("true", "1")
    return {
        "student_name": content["student_name"],
        "recipient": email_addr,
        "subject": content["subject"],
        "body": content["body"],
        "items": open_items,
        "simulated": not sending_enabled,
    }


@router.post("/applications/{app_id}/outbox/send")
def send_email(
    app_id: str,
    payload: SendEmailPayload,
    x_role: str | None = Header(default=None),
):
    """Officer clicks Send.
    Only the admissions officer can send. Writes to Outbox table; sends via SMTP if EMAIL_SENDING=true.
    """
    if x_role != DECIDER_ROLE:
        raise HTTPException(403, "Only an admissions officer can send emails")
    with app_session() as db:
        get_app(db, app_id)
        try:
            entry = ob.send_student_email(
                db,
                app_id,
                recipient_override=payload.recipient,
                upload_base_url=payload.upload_base_url,
            )
        except PermissionError as pe:
            raise HTTPException(403, str(pe)) from pe
        except ValueError as ve:
            raise HTTPException(400, str(ve)) from ve
        except RuntimeError as re:
            raise HTTPException(502, str(re)) from re

        return {
            "id": entry.id,
            "status": entry.status,
            "simulated": entry.simulated,
            "recipient": entry.recipient,
            "original_recipient": entry.original_recipient,
            "sent_at": entry.sent_at.isoformat(),
            "items_count": len(entry.items),
        }


@router.get("/applications/{app_id}/outbox")
def list_outbox(app_id: str):
    """Get the sent outbox history for this application."""
    from sqlalchemy import select
    with app_session() as db:
        get_app(db, app_id)
        entries = db.scalars(
            select(ob.OutboxMessage).where(ob.OutboxMessage.app_id == app_id).order_by(ob.OutboxMessage.sent_at.desc())
        ).all()
        return [
            {
                "id": e.id,
                "recipient": e.recipient,
                "original_recipient": e.original_recipient,
                "subject": e.subject,
                "body": e.body,
                "items": e.items,
                "status": e.status,
                "error": e.error,
                "simulated": e.simulated,
                "sent_at": e.sent_at.isoformat(),
            }
            for e in entries
        ]


@router.post("/reply")
async def reply_upload(
    token: str = Form(...),
    file: UploadFile = File(...),
    note: str = Form(""),
):
    """Student uploads a requested document via one-time token link.
    Marks item RECEIVED and resumes only the affected row.
    """
    content = await file.read()
    with app_session() as db:
        try:
            res = ob.handle_reply_upload(db, token=token, filename=file.filename or "upload.pdf", content=content, note=note)
        except ValueError as ve:
            raise HTTPException(400, str(ve)) from ve
        except Exception as e:
            raise HTTPException(500, f"Upload processing failed: {e}") from e
    return res


@router.get("/reply/{token}")
def check_reply_token(token: str):
    """Check if a token is valid and return request info for the upload portal."""
    from sqlalchemy import select
    from ..db.models import RowState
    with app_session() as db:
        states = db.scalars(select(RowState)).all()
        for st in states:
            for r in (st.requests or []):
                if r.get("token") == token or r.get("id") == token:
                    app = db.get(Application, st.app_id)
                    rec = load_record(getattr(app, "case_number", "") or "") if getattr(app, "case_number", "") else None
                    course_title = next((row.get("title") for row in (app.rows or []) if row.get("id") == st.row_id), st.row_id) if app else st.row_id
                    return {
                        "valid": True,
                        "request_id": r.get("id"),
                        "what": r.get("what"),
                        "why": r.get("why"),
                        "type": r.get("type"),
                        "status": r.get("status"),
                        "course": course_title,
                        "student_name": (rec or {}).get("name") or (app.student if app else ""),
                    }
        raise HTTPException(404, "Invalid or expired token")


class SyllabusComparePayload(BaseModel):
    target_code: str = Field(min_length=2, max_length=20)


class SyllabusReviewPayload(BaseModel):
    action: str = Field(pattern="^(accept|reject)$")
    add_to_mapping: bool = False
    aud_code: str | None = None
    credits: int = 3


@router.post("/applications/{app_id}/rows/{row_id}/syllabus-compare")
def run_syllabus_comparison(
    app_id: str,
    row_id: str,
    payload: SyllabusComparePayload,
    x_role: str | None = Header(default=None),
):
    """Run syllabus comparison for a row against an AUD target course."""
    from ..services import syllabus_compare as sc
    if x_role != DECIDER_ROLE:
        raise HTTPException(403, "Only an admissions officer can trigger syllabus comparison")
    with app_session() as db:
        get_app(db, app_id)
        try:
            return sc.compare_syllabus_for_row(db, app_id, row_id, payload.target_code)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e


@router.post("/applications/{app_id}/rows/{row_id}/syllabus-review")
def review_syllabus_proposal(
    app_id: str,
    row_id: str,
    payload: SyllabusReviewPayload,
    x_role: str | None = Header(default=None),
    x_actor: str | None = Header(default=None),
):
    """Admissions officer accepts or rejects the syllabus proposal.
    If accepting with add_to_mapping=True, adds a versioned entry to course_mappings.
    """
    from ..services import syllabus_compare as sc
    if x_role != DECIDER_ROLE:
        raise HTTPException(403, "Only an admissions officer can accept or reject syllabus proposals")
    with app_session() as db:
        get_app(db, app_id)
        try:
            return sc.officer_review_syllabus(
                db,
                app_id,
                row_id,
                action=payload.action,
                add_to_mapping=payload.add_to_mapping,
                aud_code=payload.aud_code,
                credits=payload.credits,
                actor=x_actor or "Admissions Officer",
            )
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
