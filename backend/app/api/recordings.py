"""Recorded runs: save a finished real run, list recordings, and start a replay that loads them without calling the model."""
import uuid
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..config import get_settings
from ..db.models import Application, Job, JobUnit
from ..db.session import app_session
from ..services import recording
from ..services.documents import UploadError, save_upload
from ..workers import store
from .common import DECIDER_ROLE, app_summary, get_app

router = APIRouter(prefix="/api/v1")


class SaveIn(BaseModel):
    app_id: str
    name: str


class ReplayIn(BaseModel):
    live: list[str] = []  # row ids or subject titles to run live with the model; everything else loads from the recording


@router.get("/recordings")
def list_recordings():
    return recording.listing()


@router.post("/recordings")
def save_recording(body: SaveIn, x_role: str | None = Header(default=None)):
    if x_role != DECIDER_ROLE:
        raise HTTPException(403, "Only an admissions officer can record a run")
    with app_session() as db:
        get_app(db, body.app_id)
        try:
            return recording.save(db, body.app_id, body.name)
        except ValueError as e:
            raise HTTPException(409, str(e)) from e


@router.post("/recordings/{name}/replay")
def start_replay(name: str, body: ReplayIn | None = None, x_role: str | None = Header(default=None)):
    """Create an application at the officer gate from a recording. Nothing is evaluated until the officer confirms; then each
    subject is loaded from the recording (labelled as a replay) except those listed in `live`, which the model runs now."""
    if x_role != DECIDER_ROLE:
        raise HTTPException(403, "Only an admissions officer can start a replay")
    try:
        rec = recording.load(name)
    except FileNotFoundError as e:
        raise HTTPException(404, "Unknown recording") from e
    rows = [{k: v for k, v in r.items() if k != "source_id"} for r in rec["rows"]]
    wanted = [w.strip().lower() for w in (body.live if body else [])]
    live_ids = [r["id"] for r in rows if r["id"].lower() in wanted or (r.get("title") or "").strip().lower() in wanted]
    unknown = [w for w in wanted if not any(r["id"].lower() == w or (r.get("title") or "").strip().lower() == w for r in rows)]
    if unknown:
        raise HTTPException(422, f"No such subject in the recording: {', '.join(unknown)}")
    folder = Path(get_settings().recordings_root).resolve() / recording.safe_name(name)
    scan = next(iter(folder.glob("original.*")), None)
    doc = None
    if scan:
        try:
            doc = save_upload(get_settings().document_root, scan.name, scan.read_bytes(), 40, 60)
        except UploadError:
            doc = None
    info = {"name": rec["name"], "label": recording.label(rec), "recorded_at": rec["recorded_at"], "model": rec["model"], "live": live_ids}
    with app_session() as db:
        app = Application(id=f"APP-{uuid.uuid4().hex[:8].upper()}", kind="upload", student=f"Replay of {rec['name']}", status="EXTRACTION_REVIEW_REQUIRED",
                          document=doc, extraction={**rec["reading"]["extraction"], "replay": info}, rows=rows,
                          extracted_rows=rec["extracted_rows"], qualification_hint="")
        job = Job(id=uuid.uuid4().hex[:12], app_id=app.id, phase="READING", status="DONE", stage="gate", route="replay", model=rec["model"],
                  summary={"replay": info}, started_at=store.now(), finished_at=store.now(), flags=["REPLAY"])
        db.add_all([app, job])
        db.flush()
        for seq, u in enumerate(rec["reading"]["units"]):
            db.add(JobUnit(app_id=app.id, job_id=job.id, stage="read", unit_key=u["unit_key"], seq=seq, status=u["status"], attempts=u["attempts"],
                           method="replay", duration_s=u["duration_s"], error_code=u["error_code"]))
        db.commit()
        store.audit(db, app.id, x_role, "replay_started", rec["name"], live=len(live_ids), recorded_at=rec["recorded_at"])
        return {**app_summary(db, app), "job_id": job.id}
