"""Upload, extraction, the editable verification table, curriculum linking and page preview."""
import asyncio
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from ..config import get_settings
from ..services.curriculum import curriculum
from ..services.documents import UploadError, render_page_png, save_upload
from ..services.extraction import extract_transcript
from . import state as st
from .evaluation import app_summary, get_app

router = APIRouter(prefix="/api/v1")
AUTO_LINK_MIN = 0.8


def _subject(s: dict[str, Any]) -> dict[str, Any]:
    return {"id": s["id"], "name": s["name"], "code": s["code"], "qualification": s["qualification"]}


def _candidates(row: dict[str, Any], qualification: str | None) -> list[dict[str, Any]]:
    return curriculum.candidates(row["title"], row["code"], qualification)


def _autolink(row: dict[str, Any], qualification: str | None) -> None:
    cands = _candidates(row, qualification)
    row["candidates"] = cands
    if row.get("subject") or not cands:
        return
    top = cands[0]
    unambiguous = len(cands) == 1 or top["score"] - cands[1]["score"] >= 0.05 or (top["score"] >= 0.99 > cands[1]["score"])
    if top["score"] >= AUTO_LINK_MIN and unambiguous:
        row["subject"] = _subject(top)
        row["auto_linked"] = True


def _detail(app: st.AppState) -> dict[str, Any]:
    return {**app_summary(app), "document": app.document and {k: v for k, v in app.document.items() if k != "path"},
            "extraction": app.extraction, "rows": app.rows,
            "curriculum": {"available": bool(curriculum._subjects) or curriculum.error is None, "error": curriculum.error}}


@router.get("/applications/{app_id}")
def get_application(app_id: str):
    return _detail(get_app(app_id))


@router.post("/applications")
async def create_application(file: UploadFile = File(...),  # noqa: B008
                             student_label: str = Form(""), program: str = Form(""),
                             qualification: str = Form(""), x_role: str | None = Header(default=None)):
    s = get_settings()
    data = await file.read()
    try:
        doc = save_upload(s.document_root, file.filename or "upload", data, 15, 40)
    except UploadError as e:
        raise HTTPException(400, str(e)) from e
    result = await asyncio.to_thread(extract_transcript, doc["path"], doc["id"])
    app = st.S.new_upload(student_label.strip(), program.strip())
    app.document = doc
    app.extraction = {"method": result.method, "page_count": result.page_count, "warnings": result.warnings,
                      "unparsed_lines": result.unparsed_lines[:40], "rows_found": len(result.rows)}
    for ln in result.lines:  # every raw line is kept as citable evidence
        st.S.repo.store.add(ln["id"], f"transcript-{doc['id'][:8]}", ln["page"], ln["text"])
    await asyncio.to_thread(curriculum.subjects)  # warm the cache once
    for r in result.rows:
        row = {"id": uuid.uuid4().hex[:8], "code": r.code, "title": r.title, "title_ar": r.title_ar,
               "credits": str(r.credits) if r.credits is not None else "", "grade": r.grade or "", "term": r.term,
               "page": r.page, "evidence_ref": r.evidence_ref, "uncertain_fields": r.uncertain_fields,
               "subject": None, "candidates": [], "included": True, "corrected": False}
        _autolink(row, qualification or None)
        app.rows.append(row)
    st.S.log(x_role or "unknown", "transcript_uploaded", app.id, sha256=doc["sha256"], rows=len(app.rows), method=result.method)
    return _detail(app)


class RowPatch(BaseModel):
    code: str | None = None
    title: str | None = None
    credits: str | None = None
    grade: str | None = None
    term: str | None = None
    subject_id: str | None = None  # "" unlinks
    included: bool | None = None


def _find_row(app: st.AppState, row_id: str) -> dict[str, Any]:
    row = next((r for r in app.rows if r["id"] == row_id), None)
    if not row:
        raise HTTPException(404, "Unknown row")
    return row


def _lock(app: st.AppState) -> None:
    if app.kind == "demo":
        raise HTTPException(400, "The demo application is read-only")
    if st.S.active_job and app.latest_job == st.S.active_job:
        raise HTTPException(409, "An evaluation is running for this application")


@router.patch("/applications/{app_id}/rows/{row_id}")
def patch_row(app_id: str, row_id: str, body: RowPatch, x_actor: str | None = Header(default=None)):
    app = get_app(app_id)
    _lock(app)
    row = _find_row(app, row_id)
    changes = body.model_dump(exclude_none=True)
    if "credits" in changes and changes["credits"].strip():
        try:
            Decimal(changes["credits"])
        except InvalidOperation as e:
            raise HTTPException(422, "Credits must be a number") from e
    for f in ("code", "title", "credits", "grade", "term"):
        if f in changes:
            st.S.log(x_actor or "officer", "row_corrected", f"{app_id}/{row_id}", field=f, old=row[f], new=changes[f])
            row[f] = changes[f].strip()
            row["uncertain_fields"] = [u for u in row["uncertain_fields"] if u != f]
            row["corrected"] = True
    if "included" in changes:
        row["included"] = changes["included"]
    if "subject_id" in changes:
        if changes["subject_id"] == "":
            row["subject"] = None
        else:
            s = next((x for x in curriculum.subjects() if x.id == changes["subject_id"]), None)
            if not s:
                raise HTTPException(404, "Unknown curriculum subject")
            row["subject"] = _subject(s.__dict__)
        row["auto_linked"] = False
        st.S.log(x_actor or "officer", "subject_linked", f"{app_id}/{row_id}", subject=row["subject"] and row["subject"]["name"])
    if {"title", "code"} & changes.keys():
        row["candidates"] = _candidates(row, None)
    return row


@router.post("/applications/{app_id}/rows")
def add_row(app_id: str):
    app = get_app(app_id)
    _lock(app)
    row = {"id": uuid.uuid4().hex[:8], "code": "", "title": "", "title_ar": "", "credits": "", "grade": "", "term": "", "page": 1,
           "evidence_ref": "", "uncertain_fields": [], "subject": None, "candidates": [], "included": True, "corrected": True}
    app.rows.append(row)
    return row


@router.delete("/applications/{app_id}/rows/{row_id}")
def delete_row(app_id: str, row_id: str):
    app = get_app(app_id)
    _lock(app)
    app.rows.remove(_find_row(app, row_id))
    return {"deleted": True}


class LinkIn(BaseModel):
    qualification: str | None = None
    relink: bool = False


@router.post("/applications/{app_id}/link")
async def link_subjects(app_id: str, body: LinkIn):
    app = get_app(app_id)
    _lock(app)
    await asyncio.to_thread(curriculum.subjects)
    for row in app.rows:
        if body.relink and row.get("auto_linked"):
            row["subject"] = None
        _autolink(row, body.qualification)
    return _detail(app)


@router.get("/applications/{app_id}/preview/{page}")
def preview(app_id: str, page: int):
    app = get_app(app_id)
    if not app.document:
        raise HTTPException(404, "No document")
    try:
        return Response(render_page_png(app.document["path"], page), media_type="image/png")
    except UploadError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/curriculum/qualifications")
async def qualifications():
    await asyncio.to_thread(curriculum.subjects)
    return {"qualifications": curriculum.qualifications(), "available": curriculum.error is None, "error": curriculum.error}


@router.get("/curriculum/subjects")
async def subjects(q: str = "", qualification: str | None = None, limit: int = 20):
    await asyncio.to_thread(curriculum.subjects)
    return curriculum.search(q, qualification, min(limit, 50))
