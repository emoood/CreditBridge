"""Demo mode helpers: which cases have a prepared run, and the stage speed of a demo application."""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ..config import get_settings
from ..db.session import app_session
from ..services import prepared
from ..services import student_record as sr
from ..workers import store
from .common import DECIDER_ROLE, get_app

router = APIRouter(prefix="/api/v1")


@router.get("/demo/cases")
def demo_cases():
    """Cases that have a prepared run (the picker in Demo mode lists only these), with their file name and whether a misread variant exists."""
    out: dict[str, dict] = {}
    for r in prepared.listing():
        rec = sr.load_record(r["case_number"]) or {}
        c = out.setdefault(r["case_number"], {"case_number": r["case_number"], "name": rec.get("name"), "program": rec.get("program"), "student_id": rec.get("student_id"),
                                              "filename": r["source"]["filename"], "run": None, "misread_run": None})
        c["misread_run" if r["planted"]["misread"] else "run"] = {"name": r["name"], "label": r["label"], "recorded_at": r["recorded_at"], "model": r["model"]}
    return {"demo_mode": get_settings().demo_mode, "stage_seconds": get_settings().demo_stage_seconds, "cases": sorted(out.values(), key=lambda c: c["case_number"])}


class SpeedIn(BaseModel):
    speed: str


@router.post("/applications/{app_id}/demo/speed")
def set_speed(app_id: str, body: SpeedIn, x_role: str | None = Header(default=None)):
    if x_role != DECIDER_ROLE:
        raise HTTPException(403, "Only an admissions officer can change this")
    if body.speed not in ("instant", "fast"):
        raise HTTPException(422, "speed is instant or fast")
    with app_session() as db:
        app = get_app(db, app_id)
        demo = (app.extraction or {}).get("demo")
        if not demo:
            raise HTTPException(409, "This application is not a Demo mode run")
        store.set_json(app, "extraction", {**app.extraction, "demo": {**demo, "speed": body.speed}})
        db.commit()
        return {"speed": body.speed}
