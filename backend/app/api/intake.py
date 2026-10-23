"""Upload (returns a job id at once), the verification table, curriculum linking, and the officer gate (confirm)."""
import asyncio
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from .. import runtime
from ..agents import intake_agent as ia
from ..config import get_settings
from ..db.models import Application, Job
from ..db.session import app_session
from ..services import extraction_checks as xc
from ..services import grade_checks as gc
from ..services import prepared, usage
from ..services import student_record as sr
from ..services.curriculum import curriculum
from ..services.documents import UploadError, render_page_png, save_upload
from ..services.linking import autolink, candidates, subject_ref
from ..workers import store
from ..workers.queue import worker
from .common import DECIDER_ROLE, app_summary, get_app, short_id

router = APIRouter(prefix="/api/v1")
EDITABLE = ("code", "title", "credits", "grade", "term")
# PLANTED FOR DEMO presets (demo mode only): B changes a mark, D changes a letter grade. Both are replaced by a wrong value before the extraction checks run.
PLANTS = {"B": {"subject": "AP Chemistry", "field": "mark", "wrong": 65}, "D": {"subject": "AP Statistics", "field": "letter", "wrong": "B"}, "A2": {"subject": "AP Chemistry", "field": "letter", "wrong": "C+"}}
PLANT_FOR_CASE = {"CB-2026-0105": "D", "CB-2026-0104": "A2"}


def header_view(app: Application) -> dict[str, Any]:
    """The extraction header as the officer sees it: what Intake read (stored in extraction.intake.name_check.header) with the officer's edits laid over it. Student type,
    the comparison with the case record and the missing-major mark are worked out here, from the value as it stands now."""
    ex = app.extraction or {}
    agent = ((ex.get("intake") or {}).get("name_check") or {}).get("header") or {}
    edits = ex.get("header") or {}
    value = edits["value"] if "value" in edits else agent.get("value")
    typ = xc.student_type_of(value)
    major = (edits["major"] if "major" in edits else agent.get("major")) if typ == "university" else None
    record = (ex.get("student_record") or {}).get("curriculum") or app.qualification_hint
    return {"value": value, "student_type": typ, "major": major, "major_missing": typ == "university" and not major,
            "source": "officer" if "value" in edits else agent.get("source"), "major_source": "officer" if "major" in edits else agent.get("major_source"),
            "basis": None if "value" in edits else agent.get("basis"), "disagrees": None if "value" in edits else agent.get("disagrees"),
            "curriculum_check": xc.curriculum_check(record, value) if typ != "university" else {"status": "unchecked", "record": xc.curriculum_of(record), "seen": value},
            "edited": edits.get("edited") or {}}


def _detail(db, app: Application) -> dict[str, Any]:
    extraction, rows = app.extraction, app.rows
    if app.kind == "upload" and extraction is not None:  # the header, and what credits mean for this student type, are computed when read so an edit takes effect at once
        header = header_view(app)
        cv = xc.credits_view(header["student_type"], rows)
        rows = [{**r, "credits_missing": r["id"] in cv["missing"]} for r in rows]
        extraction = {**extraction, "header": header, "credits": {"show": cv["show"], "required": cv["required"], "missing": len(cv["missing"])}}
    return {**app_summary(db, app), "document": app.document and {k: v for k, v in app.document.items() if k != "path"},
            "extraction": extraction, "rows": rows, "corrections": app.corrections,
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
                             student_label: str = Form(""), student_id: str = Form(""), plant_misread: str = Form(""), program: str = Form(""), qualification: str = Form(""), case_number: str = Form(""), demo: str = Form(""), demo_run: str = Form(""), demo_speed: str = Form("fast"),
                             x_role: str | None = Header(default=None)):
    """Store + hash the file, create the application and the reading job, return immediately. Reading happens in the worker."""
    data = await file.read()
    try:
        doc = save_upload(get_settings().document_root, file.filename or "upload", data, 15, 40)
    except UploadError as e:
        raise HTTPException(400, str(e)) from e
    run = None
    if demo.strip().lower() in ("1", "true", "on"):  # Demo mode: the model's side comes from the prepared run for this file; nothing is invented
        if not get_settings().demo_mode:
            raise HTTPException(404, "Demo mode is off")
        wants_misread = plant_misread.strip().lower() in ("1", "true", "on")
        run = prepared.find(doc["sha256"], file.filename or "", wants_misread, demo_run.strip())
        if run is None:
            raise HTTPException(404, "That prepared run was not recorded for this file" if demo_run.strip() else "No prepared run for this misread variant of this file" if wants_misread and prepared.find(doc["sha256"], file.filename or "") else "No prepared run for this file")
        case_number = case_number.strip() or run["case_number"]
    try:
        rec = sr.load_record(case_number) if case_number.strip() else None
    except sr.UnknownProgram as e:
        raise HTTPException(422, str(e)) from e
    if case_number.strip() and rec is None:
        raise HTTPException(404, f"No student record for case number {case_number.strip()}")
    if rec:  # the case number loads name, ID, contact, program, term and curriculum from the (demo) student data
        student_label, student_id, program, qualification = rec["name"], rec["student_id"], rec["program"], qualification or rec["curriculum"]
    with app_session() as db:
        app = Application(id=f"APP-{uuid.uuid4().hex[:8].upper()}", kind="upload", student=student_label.strip() or "Uploaded transcript",
                          program=program.strip(), status="READING", document=doc, qualification_hint=qualification, extraction={"student_id": student_id.strip(), **({"student_record": rec} if rec else {}), **({"demo": {"run": run["name"], "speed": demo_speed if demo_speed in ("instant", "fast") else "fast", "label": run["label"]}} if run else {}), **({"planted_misread": PLANTS[PLANT_FOR_CASE.get(rec["case_number"] if rec else "", "B")]} if (plant_misread.strip().lower() in ("1", "true", "on") or (run and run["planted"].get("misread"))) and get_settings().demo_mode else {})})
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


class HeaderPatch(BaseModel):
    value: str | None = None  # "Institution / curriculum"; blank clears it. Only the fields sent are changed
    major: str | None = None


@router.patch("/applications/{app_id}/header")
def patch_header(app_id: str, body: HeaderPatch, x_actor: str | None = Header(default=None)):
    """The officer sets or corrects the header. Intake never overwrites it afterwards; the field then shows who edited it and when."""
    with app_session() as db:
        app = _editable(db, app_id)
        ex = dict(app.extraction or {})
        edits = dict(ex.get("header") or {})
        edited = dict(edits.get("edited") or {})
        who = x_actor or "officer"
        for f in ("value", "major"):
            if f in body.model_fields_set:
                v = getattr(body, f)
                edits[f] = xc.normalise_header_value(v) if f == "value" else ((v or "").strip()[:120] or None)
                edited[f] = {"by": who, "at": datetime.now(UTC).isoformat()}
                store.audit(db, app_id, who, "header_corrected", app_id, field=f)  # values are not logged
        store.set_json(app, "extraction", {**ex, "header": {**edits, "edited": edited}})
        db.commit()
        return header_view(app)


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
                row["edited"] = {**(row.get("edited") or {}), f: {"by": x_actor or "officer", "at": datetime.now(UTC).isoformat(), "via": "typed"}}  # AI never replaces this field
                if (row.get("reeval") or {}).get(f):
                    row["reeval"] = {k: v for k, v in row["reeval"].items() if k != f}
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


# ----------------------------------------------------------------------------------------------- re-evaluate (chunk C1, step 3)
class ReevalIn(BaseModel):
    scope: Literal["field", "row", "page"] = "field"
    row_id: str | None = None
    field: str | None = None
    page: int | None = None
    confirm_overwrite: bool = False  # the officer agreed to re-read fields they edited themselves


def _reading_units(db, app: Application) -> list[tuple[str, dict[str, Any]]]:
    """(block key, stored reading) of the finished blocks of the latest reading, in the order the table was built from."""
    job = db.scalar(select(Job).where(Job.app_id == app.id, Job.phase == "READING").order_by(Job.created_at.desc()))
    return [(u.unit_key, u.result) for u in store.units_of(db, job.id) if u.stage == "read" and u.status == "DONE" and u.result] if job else []


def _scale(app: Application) -> dict[str, Any]:
    return gc.load_scale(((app.extraction or {}).get("student_record") or {}).get("grade_scale"))


@router.post("/applications/{app_id}/reevaluate")
async def reevaluate(app_id: str, body: ReevalIn, x_actor: str | None = Header(default=None)):
    """Read a cell, a row or a page again at a higher zoom, WITHOUT showing the model what the table says now. Nothing in the table changes: each differing value is
    stored on its row as a proposal (old and new side by side) for the officer to pick. Fields the officer edited are skipped unless `confirm_overwrite` is set."""
    who = x_actor or "officer"
    with app_session() as db:
        app = _editable(db, app_id)
        ex = app.extraction or {}
        if ex.get("demo"):
            raise HTTPException(409, "Re-evaluate needs Live AI. This case replays a recording, which has no answer for a new question.")
        if not app.document:
            raise HTTPException(404, "No document")
        path, scale = app.document["path"], _scale(app)
        rows = {r["id"]: dict(r) for r in app.rows}
        ids = [r["id"] for r in app.extracted_rows]
        units = _reading_units(db, app)
        used = (ex.get("reeval") or {}).get("calls", 0)
    stored = dict(units)
    located = ia.locate_rows(units, ids)
    if located is None:
        raise HTTPException(409, "This extraction cannot be matched to the page blocks, so nothing can be re-read. Edit by hand.")
    fields: tuple[str, ...]
    if body.scope == "page":
        if not body.page:
            raise HTTPException(422, "Give the page to re-read")
        keys = [k for k in stored if k.startswith(f"p{body.page}b") and ia.course_rows(stored[k])]
        wanted = [(rid, ("title", "grade", "credits", "term")) for rid, (k, _p) in located.items() if k in keys and rid in rows]
        positions: dict[str, int | None] = {}
        obj = f"page {body.page}"
    else:
        if body.row_id not in rows or body.row_id not in located:
            raise HTTPException(404 if body.row_id not in rows else 409, "Unknown row" if body.row_id not in rows else "This row was added by hand: there is nothing on the page to re-read.")
        key, pos = located[body.row_id]
        if body.scope == "field":
            if body.field not in ia.REEVAL_FIELDS:
                raise HTTPException(422, f"field must be one of {', '.join(ia.REEVAL_FIELDS)}")
            fields = (body.field,)
            hit = (rows[body.row_id].get("edited") or {}).get(body.field)
            if hit and not body.confirm_overwrite:
                raise HTTPException(409, f"Edited by {hit['by']} on {hit['at'][:10]}. Re-read it anyway? The edited value is kept unless you choose the new one.")
        else:
            fields = ("title", "grade", "credits", "term")
        keys, wanted, positions, obj = [key], [(body.row_id, fields)], {key: pos}, body.row_id
    if not keys:
        return {"proposals": [], "notes": [{"why": "no course rows on that page"}], "calls_used": used, "calls_budget": ia.REEVAL_CALL_BUDGET}
    if used + len(keys) > ia.REEVAL_CALL_BUDGET:
        raise HTTPException(409, f"The re-evaluation budget for this case is used ({used} of {ia.REEVAL_CALL_BUDGET} model calls). Edit by hand.")
    t0 = time.monotonic()
    try:
        fresh = await ia.reread_blocks(usage.MeteredProvider(runtime.get_provider(), app_id, body.row_id or ""), path, keys, scale, positions)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    except Exception as e:  # the provider is down or timed out: nothing was changed
        usage.log_tool(app_id, body.row_id or "", "intake", "reevaluate", {"scope": body.scope, "blocks": keys}, False, type(e).__name__, time.monotonic() - t0)
        raise HTTPException(502, "The re-read failed. Nothing was changed.") from e
    props, notes = ia.build_proposals(rows, located, stored, fresh, wanted, body.confirm_overwrite)
    at = datetime.now(UTC).isoformat()
    with app_session() as db:
        app = _editable(db, app_id)
        table = [dict(r) for r in app.rows]
        for r in table:
            for p in (p for p in props if p["row"] == r["id"]):
                r["reeval"] = {**(r.get("reeval") or {}), p["field"]: {**p, "by": who, "at": at}}
        ex = dict(app.extraction or {})
        ex["reeval"] = {**(ex.get("reeval") or {}), "calls": used + len(keys)}
        store.set_json(app, "rows", table)
        store.set_json(app, "extraction", ex)
        db.commit()
        store.audit(db, app_id, who, "reevaluate", obj, scope=body.scope, blocks=len(keys), proposals=sum(1 for p in props if not p["same"]), agreed=sum(1 for p in props if p["same"]))
    usage.log_tool(app_id, body.row_id or "", "intake", "reevaluate", {"scope": body.scope, "blocks": keys}, True, f"{len(props)} values compared", time.monotonic() - t0)
    return {"proposals": [{k: v for k, v in p.items() if k != "reading"} for p in props], "notes": notes, "calls_used": used + len(keys), "calls_budget": ia.REEVAL_CALL_BUDGET}


class ReevalChoice(BaseModel):
    choice: Literal["new", "old"]


@router.post("/applications/{app_id}/rows/{row_id}/reeval/{field}")
def resolve_reeval(app_id: str, row_id: str, field: str, body: ReevalChoice, x_actor: str | None = Header(default=None)):
    """The officer picks between the value in the table and the re-read one. Picking the new one puts it in the table as the officer's decision, then code checks the row
    again, recomputes the term and cumulative totals and refreshes the curriculum candidates (no model call)."""
    who = x_actor or "officer"
    with app_session() as db:
        app = _editable(db, app_id)
        rows = [dict(r) for r in app.rows]
        row = next((r for r in rows if r["id"] == row_id), None)
        if row is None:
            raise HTTPException(404, "Unknown row")
        prop = (row.get("reeval") or {}).get(field)
        if not prop:
            raise HTTPException(404, "There is no re-read value to choose for this field")
        ex = dict(app.extraction or {})
        if body.choice == "new" and not prop["same"]:
            at = datetime.now(UTC).isoformat()
            scale = _scale(app)
            ia.apply_proposal(row, prop, scale, who, at)
            if field in ("title", "code"):
                if row.get("auto_linked"):
                    row["subject"] = None
                autolink(row, app.qualification_hint or None)
            overrides = [*((ex.get("reeval") or {}).get("overrides") or []), {"block": prop["block"], "pos": prop["pos"], "reading": prop["reading"]}]
            merged = ia.recompute_extraction(_reading_units(db, app), overrides, scale)
            kept = [f for f in ex.get("flags", []) if f.startswith(("The name on the transcript", "curriculum_unavailable"))]
            ex = {**ex, "flags": [*merged["flags"], *kept], "cumulative": merged["cumulative"], "unrecognized": sum(1 for r in rows if not r.get("subject")),
                  "reeval": {**(ex.get("reeval") or {}), "overrides": overrides}}
            store.set_json(app, "extraction", ex)
        row["reeval"] = {k: v for k, v in row["reeval"].items() if k != field}
        if not row["reeval"]:
            del row["reeval"]
        store.set_json(app, "rows", rows)
        db.commit()
        store.audit(db, app_id, who, "reeval_chosen", row_id, field=field, choice=body.choice, changed=body.choice == "new" and not prop["same"])
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
