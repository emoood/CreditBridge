"""Finishing a case: row actions, confirm all mapped, the checked sheet, the Auditor, final approval, and the AUD course search."""
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..config import get_settings
from ..db.models import Application, Job, RowAction
from ..db.session import app_session
from ..runtime import rt
from ..services import student_record as sr
from ..services import actions as ac
from ..services import sheet as sh
from ..services import usage
from ..workers import store
from ..workers.queue import worker
from .common import DECIDER_ROLE, get_app

router = APIRouter(prefix="/api/v1")


def _officer(role: str | None) -> None:
    if role != DECIDER_ROLE:
        raise HTTPException(403, "Only an admissions officer can do this")


def _confirmed(app: Application) -> None:
    if app.kind != "upload" or app.confirmed_at is None:
        raise HTTPException(409, "The extraction has not been confirmed yet")


class ActionIn(BaseModel):
    action: str
    aud_code: str | None = None
    credits: int | None = None
    reason: str = ""


def _queue_job(db, app_id: str, phase: str, summary: dict[str, Any]) -> str:
    if any(j.status in ("QUEUED", "RUNNING") for j in db.scalars(select(Job).where(Job.app_id == app_id))):
        raise HTTPException(409, "This application is still being worked on; try again in a moment")
    job = Job(id=uuid.uuid4().hex[:12], app_id=app_id, phase=phase, status="QUEUED", summary=summary)
    db.add(job)
    db.commit()
    return job.id


@router.post("/applications/{app_id}/rows/{row_id}/action")
def row_action(app_id: str, row_id: str, body: ActionIn, x_role: str | None = Header(default=None), x_actor: str | None = Header(default=None)):
    _officer(x_role)
    actor = x_actor or "admissions officer"
    job_id = None
    with app_session() as db:
        app = get_app(db, app_id)
        _confirmed(app)
        row = next((r for r in app.rows if r["id"] == row_id), None)
        if row is None:
            raise HTTPException(404, "Unknown row")
        prev = ac.latest(db, app_id).get(row_id)
        if body.action == "CONFIRM" and prev and prev.action == "CONFIRM":
            return ac.action_dict(prev)  # replayed click: nothing new to record
        try:
            a = ac.record(db, app, row, body.action, actor, x_role or "", body.aud_code, body.credits, body.reason)
        except ac.ActionError as e:
            raise HTTPException(e.status, e.msg) from e
        store.audit(db, app_id, actor, f"action_{body.action.lower()}", row_id)  # the reason stays in row_actions
        if body.action == "SEND_FOR_REVIEW":
            usage.trace(app_id, row_id, "code", "sent_for_review", {})
            job_id = _queue_job(db, app_id, "CASE", {"row_id": row_id, "mode": "review_package"})
        out = ac.action_dict(a)
    if job_id:
        worker.enqueue(job_id)
        out["job_id"] = job_id
    return out


@router.post("/applications/{app_id}/confirm-mapped")
def confirm_mapped(app_id: str, x_role: str | None = Header(default=None), x_actor: str | None = Header(default=None)):
    _officer(x_role)
    with app_session() as db:
        app = get_app(db, app_id)
        _confirmed(app)
        made = ac.confirm_all_mapped(db, app, x_actor or "admissions officer", x_role or "")
        store.audit(db, app_id, x_actor or "officer", "confirm_all_mapped", "", rows=len(made))
        return {"confirmed": len(made)}


@router.get("/applications/{app_id}/sheet")
def get_sheet(app_id: str):
    with app_session() as db:
        app = get_app(db, app_id)
        out = sh.build(db, app)
        out["audit"] = (app.extraction or {}).get("audit")
        out["unresolved"] = ac.unresolved(db, app)
        out["approved"] = app.status == "DECISIONS_RECORDED" and any(a.action == "APPROVE" for a in db.scalars(select(RowAction).where(RowAction.app_id == app_id)))
        return out


class PlantIn(BaseModel):
    on: bool = True


@router.post("/applications/{app_id}/demo/plant-copy-error")
def plant_copy_error(app_id: str, body: PlantIn, x_role: str | None = Header(default=None)):
    """PLANTED FOR DEMO. Changes one confirmed AUD equivalent in the OUTPUT stage only (the stored decisions are untouched), so the normal sheet
    checks and the Auditor have something real to catch. Refused when demo_mode is off."""
    _officer(x_role)
    if not get_settings().demo_mode:
        raise HTTPException(404, "Demo switches are off")
    with app_session() as db:
        app = get_app(db, app_id)
        _confirmed(app)
        ex = dict(app.extraction or {})
        if not body.on:
            ex.pop("planted_copy_error", None)
            store.set_json(app, "extraction", ex)
            db.commit()
            usage.trace(app_id, "", "code", "planted_for_demo_removed", {})
            return {"planted": None}
        done = ac.latest(db, app_id)
        row = next((r for r in app.rows if done.get(r["id"]) and done[r["id"]].action == "CONFIRM"), None)
        if row is None:
            raise HTTPException(409, "Confirm at least one mapped course first")
        src = done[row["id"]].chosen["aud_code"]
        tg = rt().repo.targets
        other = next((c for c in tg if c != src and c.split()[0] == src.split()[0] and tg[c]["level"] <= 200), None) or next(c for c in tg if c != src)
        ex["planted_copy_error"] = {"row_id": row["id"], "source_course": row.get("title"), "from": src, "to": other, "label": "planted for demo"}
        store.set_json(app, "extraction", ex)
        db.commit()
        usage.trace(app_id, row["id"], "code", "planted_for_demo", {"from": src, "to": other, "note": "PLANTED FOR DEMO: one confirmed AUD equivalent changed in the output stage"})
        return {"planted": ex["planted_copy_error"]}


@router.post("/applications/{app_id}/approve")
def approve(app_id: str, x_role: str | None = Header(default=None), x_actor: str | None = Header(default=None)):
    _officer(x_role)
    with app_session() as db:
        app = get_app(db, app_id)
        _confirmed(app)
        left = ac.unresolved(db, app)
        if left:
            raise HTTPException(409, f"{len(left)} relevant course(s) are not resolved yet: " + ", ".join(x["title"] or "?" for x in left[:5]))
        sheet = sh.build(db, app)
        if not sheet["ok"]:
            raise HTTPException(409, f"{len(sheet['checks'])} sheet check(s) fail; fix them or run the Auditor first")
        db.add(RowAction(app_id=app_id, row_id="", action="APPROVE", actor=x_actor or "admissions officer", actor_role=x_role or "", original={}, chosen={}, reason=""))
        app.status = "DECISIONS_RECORDED"
        db.commit()
        store.audit(db, app_id, x_actor or "officer", "approved", "", lines=len(sheet["rows"]))
        return {"approved": True}


@router.post("/applications/{app_id}/audit")
def run_audit_job(app_id: str, x_role: str | None = Header(default=None)):
    """Queue the Auditor for the sheet as it stands now (it runs on every export, clean or not)."""
    _officer(x_role)
    with app_session() as db:
        app = get_app(db, app_id)
        _confirmed(app)
        job_id = _queue_job(db, app_id, "AUDIT", {})
    worker.enqueue(job_id)
    return {"job_id": job_id}


@router.post("/applications/{app_id}/export-check")
def export_check(app_id: str, x_role: str | None = Header(default=None)):
    """Called before every copy or download. The Auditor reviews the sheet unless it already reviewed this exact sheet (same hash) and found it clean;
    returns {ready: true} to go ahead, or {job_id} to wait for the Auditor and then read the sheet again."""
    _officer(x_role)
    with app_session() as db:
        app = get_app(db, app_id)
        _confirmed(app)
        sheet = sh.build(db, app)
        audit = (app.extraction or {}).get("audit")
        if audit and audit.get("sheet_hash") == sheet["sheet_hash"] and audit.get("clean") and sheet["ok"]:
            return {"ready": True}
        job_id = _queue_job(db, app_id, "AUDIT", {})
    worker.enqueue(job_id)
    return {"ready": False, "job_id": job_id}


class ApplyIn(BaseModel):
    row_ids: list[str]


@router.post("/applications/{app_id}/audit/apply")
def apply_corrections(app_id: str, body: ApplyIn, x_role: str | None = Header(default=None), x_actor: str | None = Header(default=None)):
    """The officer applies chosen Auditor proposals. Each becomes a CORRECT action showing the original and the new value."""
    _officer(x_role)
    with app_session() as db:
        app = get_app(db, app_id)
        audit = (app.extraction or {}).get("audit")
        if not audit:
            raise HTTPException(409, "There is no Auditor report")
        done = []
        for p in audit["proposals"]:
            if p["row_id"] not in body.row_ids or p["row_id"] in audit["applied"]:
                continue
            row = next((r for r in app.rows if r["id"] == p["row_id"]), None)
            try:
                ac.record(db, app, row, "CORRECT", x_actor or "admissions officer", x_role or "", p["to"]["aud_code"], p["to"]["credits"], f"Auditor proposal: {p['why']}")
            except ac.ActionError as e:
                raise HTTPException(e.status, e.msg) from e
            done.append(p["row_id"])
        store.set_json(app, "extraction", {**app.extraction, "audit": {**audit, "applied": [*audit["applied"], *done]}})
        db.commit()
        store.audit(db, app_id, x_actor or "officer", "audit_applied", "", rows=len(done))
        return {"applied": done, "sheet_ok": sh.build(db, app)["ok"]}


@router.get("/catalog/search")
def search_catalog(q: str, limit: int = 8, program: str = ""):
    """Search AUD courses for a Change. Areas that never take transfer credit, and courses blocked for the student's program, are never offered."""
    R = rt()
    hits = R.retriever.search(q, min(limit, 3)) if q.strip() else []
    hits = [c for c in hits if not sr.restriction(R.repo.targets[c.course_id]["code"], program)]
    return [{"code": c.course_id, "title": R.repo.targets[c.course_id]["title"], "level": R.repo.targets[c.course_id]["level"]} for c in hits]
