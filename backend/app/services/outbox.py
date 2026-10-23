"""Outbox service: request items management, email draft generation,
SMTP sending with safeguards, outbox ledger, and reply upload handling.
"""
import os
import re
import secrets
import smtplib
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Callable

from dotenv import load_dotenv
load_dotenv(override=False)

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..db.models import Application, Base, Job, RowState, now
from ..services import mapping as mp
from ..services import usage
from ..services.documents import UploadError, save_upload
from ..services.student_record import load_record
from ..workers import store
from ..workers.queue import worker

REQUEST_TYPES = [
    "syllabus or outline",
    "exam score report",
    "official transcript with credits",
    "translation",
    "other",
]

TEMPLATES = [
    {
        "type": "syllabus or outline",
        "what": "Syllabus or course outline with detailed learning outcomes",
        "why": "Needed to verify content match against AUD course requirements",
    },
    {
        "type": "exam score report",
        "what": "Official AP or external exam score report",
        "why": "Needed to confirm official exam grade qualification",
    },
    {
        "type": "official transcript with credits",
        "what": "Official transcript showing credit hours and term dates",
        "why": "Needed to verify credit weighting and transfer eligibility",
    },
    {
        "type": "translation",
        "what": "Certified English translation of syllabus and transcript",
        "why": "Required for evaluation by Admissions Committee",
    },
]


def classify_request_type(what: str, explicit_type: str | None = None) -> str:
    """Determine the request item type, respecting an explicit type or classifying from text."""
    if explicit_type and explicit_type in REQUEST_TYPES:
        return explicit_type
    w = (what or "").lower()
    if any(k in w for k in ("syllabus", "outline", "learning outcome", "clo")):
        return "syllabus or outline"
    if any(k in w for k in ("exam", "score", "ap score", "test report", "college board")):
        return "exam score report"
    if any(k in w for k in ("transcript", "credit", "official grade", "hours", "grade report")):
        return "official transcript with credits"
    if any(k in w for k in ("translat", "english version", "arabic", "french")):
        return "translation"
    return "other"


def is_email_sending_enabled() -> bool:
    load_dotenv(override=False)
    return os.getenv("EMAIL_SENDING", "false").lower() in ("true", "1")


def generate_token() -> str:
    """Generate a secure one-time URL token for document upload."""
    return secrets.token_urlsafe(16)


class OutboxMessage(Base):
    """Every email send attempt: time, recipient, included items and result (sent, simulated, or error)."""
    __tablename__ = "outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    app_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    recipient: Mapped[str] = mapped_column(String(160))
    original_recipient: Mapped[str] = mapped_column(String(160), default="")
    subject: Mapped[str] = mapped_column(String(240), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    items: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="SENT")  # SENT | SIMULATED | FAILED
    error: Mapped[str] = mapped_column(Text, default="")
    simulated: Mapped[bool] = mapped_column(Boolean, default=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# Hook for injecting a mock SMTP sender in unit tests
_mock_sender: Callable[[str, str, str, str], None] | None = None


def set_smtp_sender(fn: Callable[[str, str, str, str], None] | None) -> None:
    global _mock_sender
    _mock_sender = fn


def normalize_item(req: dict, row_id: str = "", course_title: str = "") -> dict:
    """Ensure a request item has consistent fields including type, status, and token."""
    item = dict(req)
    item.setdefault("id", uuid.uuid4().hex[:8])
    item.setdefault("row_id", row_id)
    item.setdefault("course_title", course_title)
    item["type"] = classify_request_type(item.get("what", ""), item.get("type"))
    item.setdefault("status", "DRAFT")
    item.setdefault("to", "student")
    item.setdefault("why", "")
    item.setdefault("what", "")
    item.setdefault("token", generate_token())
    return item


def get_student_requests(db: Session, app_id: str) -> list[dict]:
    """Return all request items across all rows of an application."""
    app = db.get(Application, app_id)
    if not app:
        return []
    course_titles = {r["id"]: r.get("title") or r.get("code") or r["id"] for r in (app.rows or [])}
    states = db.scalars(select(RowState).where(RowState.app_id == app_id)).all()
    all_reqs = []
    for st in states:
        title = course_titles.get(st.row_id, st.row_id)
        for r in (st.requests or []):
            all_reqs.append(normalize_item(r, row_id=st.row_id, course_title=title))
    return all_reqs


def add_request_item(db: Session, app_id: str, row_id: str, what: str, why: str, req_type: str | None = None, to: str = "student") -> dict:
    """Add a new request item to a row. Refuses duplicate active drafts."""
    st = mp.get_state(db, app_id, row_id)
    if not st:
        raise ValueError(f"Unknown row state {row_id}")
    reqs = list(st.requests or [])
    t = classify_request_type(what, req_type)
    same = next((r for r in reqs if r.get("status") in ("DRAFT", "SENT") and r.get("what", "").strip().lower() == what.strip().lower()), None)
    if same:
        return normalize_item(same, row_id=row_id)
    item = {
        "id": uuid.uuid4().hex[:8],
        "what": what.strip(),
        "why": why.strip(),
        "type": t,
        "to": to,
        "status": "DRAFT",
        "token": generate_token(),
        "created_by": "officer",
    }
    updated = [*reqs, item]
    mp.save_state(db, app_id, row_id, requests=updated)
    return normalize_item(item, row_id=row_id)


def remove_request_item(db: Session, app_id: str, row_id: str, request_id: str) -> bool:
    """Remove or cancel a request item from a row."""
    st = mp.get_state(db, app_id, row_id)
    if not st or not st.requests:
        return False
    kept = [r for r in st.requests if r.get("id") != request_id]
    if len(kept) == len(st.requests):
        return False
    mp.save_state(db, app_id, row_id, requests=kept)
    return True


def edit_request_item(db: Session, app_id: str, row_id: str, request_id: str, what: str | None = None, why: str | None = None, req_type: str | None = None) -> dict | None:
    """Edit the text or type of an existing request item."""
    st = mp.get_state(db, app_id, row_id)
    if not st or not st.requests:
        return None
    found = None
    new_reqs = []
    for r in st.requests:
        if r.get("id") == request_id:
            updated = dict(r)
            if what is not None:
                updated["what"] = what.strip()
            if why is not None:
                updated["why"] = why.strip()
            if req_type is not None:
                updated["type"] = classify_request_type(updated.get("what", ""), req_type)
            found = updated
            new_reqs.append(updated)
        else:
            new_reqs.append(r)
    if found:
        mp.save_state(db, app_id, row_id, requests=new_reqs)
        return normalize_item(found, row_id=row_id)
    return None


def build_email_content(app: Application, rec: dict | None, items: list[dict], upload_base_url: str = "") -> dict[str, str]:
    """Build one grouped email for a student containing open request items."""
    student_name = (rec or {}).get("name") or app.student or "Applicant"
    open_items = [i for i in items if i.get("status") in ("DRAFT", "SENT")]
    if not open_items:
        checklist = "• No open documentation requests at this time."
        links = "No open upload links."
    else:
        check_lines = []
        link_lines = []
        for it in open_items:
            c_name = it.get("course_title") or it.get("row_id") or "Course"
            what = it.get("what", "").strip()
            why = it.get("why", "").strip()
            line = f"• {c_name}: {what}"
            if why:
                line += f" ({why})"
            check_lines.append(line)
            token = it.get("token") or it.get("id")
            if upload_base_url:
                url = f"{upload_base_url.rstrip('/')}/upload?token={token}"
            else:
                url = f"/upload?token={token}"
            link_lines.append(f"• {c_name} upload: {url}")
        checklist = "\n".join(check_lines)
        links = "\n".join(link_lines)

    subject = "Documentation needed for your AUD transfer credit evaluation"
    body = (
        f"Dear {student_name},\n\n"
        "To evaluate your transfer credit for American University in Dubai, we need additional documentation for the following course(s):\n\n"
        f"{checklist}\n\n"
        "Please submit these documents by replying to this email or by uploading them securely at:\n"
        f"{links}\n\n"
        "Please provide these materials within 10 business days to ensure timely review of your application.\n\n"
        "Sincerely,\n"
        "Office of Admissions\n"
        "American University in Dubai"
    )
    return {"subject": subject, "body": body, "student_name": student_name}


def get_student_record(app: Application | None) -> dict[str, Any] | None:
    """Return student record associated with application (from extraction dict or loaded by case_number)."""
    if not app:
        return None
    extraction = app.extraction or {}
    rec = extraction.get("student_record")
    if isinstance(rec, dict) and rec:
        return rec
    case_num = extraction.get("case_number") or getattr(app, "case_number", None)
    if not case_num and isinstance(rec, dict):
        case_num = rec.get("case_number")
    if case_num:
        try:
            return load_record(str(case_num))
        except Exception:
            return None
    return None


def get_student_email(app: Application | None) -> str:
    """Get the student's email address if available."""
    if not app:
        return ""
    rec = get_student_record(app)
    if rec and rec.get("email"):
        return str(rec["email"]).strip()
    return str((app.extraction or {}).get("student_email") or "").strip()


def send_student_email(db: Session, app_id: str, recipient_override: str | None = None, upload_base_url: str = "") -> OutboxMessage:
    """Send one email per student from all open items across their rows.
    Safeguards:
    - EMAIL_SENDING switch (default False -> simulated send).
    - Recipient allowlist check.
    - Example.invalid demo addresses redirected to test inbox with banner.
    - Result recorded in OutboxMessage table.
    - Success marks open items as SENT.
    """
    app = db.get(Application, app_id)
    if not app:
        raise ValueError(f"Application {app_id} not found")

    rec = get_student_record(app)
    items = get_student_requests(db, app_id)
    open_items = [i for i in items if i.get("status") == "DRAFT"]
    if not open_items:
        # Check if there are already SENT items to resend / reminder
        open_items = [i for i in items if i.get("status") in ("DRAFT", "SENT")]
    if not open_items:
        raise ValueError("No request items to send for this student")

    content = build_email_content(app, rec, open_items, upload_base_url=upload_base_url)
    subject = content["subject"]
    body = content["body"]

    # Target recipient
    target_email = (recipient_override or get_student_email(app) or "").strip()
    if not target_email:
        raise ValueError("Student email address is missing. Provide an email before sending.")

    # Remember email on application extraction if provided or overridden
    if target_email:
        extraction = dict(app.extraction or {})
        if extraction.get("student_email") != target_email:
            extraction["student_email"] = target_email
            store.set_json(app, "extraction", extraction)
            db.commit()

    # Environment settings
    email_sending_enabled = is_email_sending_enabled()
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "").strip()
    smtp_pass = os.getenv("SMTP_PASSWORD", "").strip()
    smtp_from = os.getenv("SMTP_FROM", smtp_user).strip() or "admissions@aud.edu"
    test_inbox = os.getenv("EMAIL_TEST_INBOX", "").strip()
    allowlist_raw = os.getenv("EMAIL_ALLOWLIST", "")
    allowlist = [e.strip().lower() for e in allowlist_raw.split(",") if e.strip()]

    # Safeguard 1: Demo / example.invalid address redirection
    original_recipient = target_email
    effective_recipient = target_email
    is_demo_address = target_email.lower().endswith(".invalid") or "example" in target_email.lower()
    if is_demo_address:
        if test_inbox:
            effective_recipient = test_inbox
            body = f"[Original Recipient: {original_recipient}]\n\n" + body
        else:
            # Without test inbox, demo invalid address must remain simulated
            email_sending_enabled = False

    # Safeguard 2: Recipient allowlist check
    if allowlist and "*" not in allowlist and effective_recipient.lower() not in allowlist:
        raise PermissionError(f"Recipient '{effective_recipient}' is not on the email allowlist.")

    outbox_entry = OutboxMessage(
        app_id=app_id,
        recipient=effective_recipient,
        original_recipient=original_recipient if original_recipient != effective_recipient else "",
        subject=subject,
        body=body,
        items=open_items,
        simulated=not email_sending_enabled,
    )

    if not email_sending_enabled:
        # Simulated send: record in outbox, update items to SENT
        outbox_entry.status = "SIMULATED"
        outbox_entry.error = ""
        db.add(outbox_entry)
        _mark_items_sent(db, app_id, open_items)
        db.commit()
        return outbox_entry

    # Real sending via SMTP
    try:
        if _mock_sender is not None:
            _mock_sender(smtp_from, effective_recipient, subject, body)
        else:
            msg = MIMEMultipart()
            msg["From"] = smtp_from
            msg["To"] = effective_recipient
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                if smtp_user and smtp_pass:
                    server.login(smtp_user, smtp_pass)
                server.sendmail(smtp_from, [effective_recipient], msg.as_string())

        outbox_entry.status = "SENT"
        outbox_entry.error = ""
        db.add(outbox_entry)
        _mark_items_sent(db, app_id, open_items)
        db.commit()
        return outbox_entry
    except Exception as exc:
        outbox_entry.status = "FAILED"
        outbox_entry.error = str(exc)
        db.add(outbox_entry)
        db.commit()
        raise RuntimeError(f"SMTP delivery failed: {exc}") from exc


def _mark_items_sent(db: Session, app_id: str, sent_items: list[dict]) -> None:
    """Update status of items from DRAFT to SENT in RowState."""
    sent_ids = {it["id"] for it in sent_items}
    states = db.scalars(select(RowState).where(RowState.app_id == app_id)).all()
    for st in states:
        if not st.requests:
            continue
        changed = False
        new_reqs = []
        for r in st.requests:
            if r.get("id") in sent_ids and r.get("status") == "DRAFT":
                new_reqs.append({**r, "status": "SENT", "token": r.get("token") or generate_token()})
                changed = True
            else:
                new_reqs.append(r)
        if changed:
            mp.save_state(db, app_id, st.row_id, requests=new_reqs)


def handle_reply_upload(db: Session, token: str, filename: str, content: bytes, note: str = "") -> dict[str, Any]:
    """Handle an uploaded document tied to a request token.
    Marks the item RECEIVED and resumes only the affected row through the one-row resume path.
    """
    # 1. Locate request by token
    states = db.scalars(select(RowState)).all()
    target_state: RowState | None = None
    target_req: dict | None = None
    for st in states:
        for r in (st.requests or []):
            if r.get("token") == token or r.get("id") == token:
                target_state = st
                target_req = r
                break
        if target_state:
            break

    if not target_state or not target_req:
        raise ValueError("Invalid or expired reply upload token.")

    app = db.get(Application, target_state.app_id)
    if not app:
        raise ValueError("Application for this request does not exist.")

    # 2. Save document upload
    from ..config import get_settings
    doc_root = get_settings().document_root
    doc = save_upload(doc_root, filename, content, 15, 40)

    # 3. Mark item RECEIVED
    row_id = target_state.row_id
    app_id = target_state.app_id
    req_type = classify_request_type(target_req.get("what", ""), target_req.get("type"))
    is_syllabus = req_type == "syllabus or outline" or "syllabus" in target_req.get("what", "").lower()

    new_facts = dict(target_state.facts or {})
    if is_syllabus:
        new_facts["syllabus"] = True

    if note.strip():
        existing_notes = new_facts.get("_notes", [])
        existing_notes.append({
            "at": store.now().isoformat(),
            "by": "student_reply",
            "text": note.strip()[:600],
            "document": True,
        })
        new_facts["_notes"] = existing_notes

    updated_reqs = []
    for r in (target_state.requests or []):
        if r.get("id") == target_req.get("id"):
            updated_reqs.append({
                **r,
                "status": "RECEIVED",
                "document": {"filename": doc["filename"], "sha256": doc["sha256"]},
                "received_at": store.now().isoformat(),
            })
        else:
            updated_reqs.append(r)

    mp.save_state(db, app_id, row_id, facts=new_facts, requests=updated_reqs, status=mp.CONDITIONAL, reason="Evidence received; re-checking")

    # 4. Resume ONLY the affected row
    # Check for active running/queued job on this app
    existing_job = db.scalars(select(Job).where(Job.app_id == app.id, Job.status.in_(("QUEUED", "RUNNING")))).first()
    if existing_job:
        job_id = existing_job.id
    else:
        job = Job(id=uuid.uuid4().hex[:12], app_id=app.id, phase="CASE", status="QUEUED", summary={"row_id": row_id})
        db.add(job)
        db.commit()
        job_id = job.id
        worker.enqueue(job_id)

    # 5. Audit & Trace
    store.audit(db, app_id, "student", "reply_received", row_id, request_id=target_req.get("id"), document=doc["filename"])
    usage.trace(app_id, row_id, "code", "reply_document_received", {"request_id": target_req.get("id"), "document": doc["filename"]})

    return {
        "ok": True,
        "app_id": app_id,
        "row_id": row_id,
        "request_id": target_req.get("id"),
        "job_id": job_id,
        "document": doc["filename"],
    }
