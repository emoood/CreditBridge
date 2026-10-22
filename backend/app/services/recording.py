"""Recorded runs: persist a finished real run (reading + evaluation) and load it later without calling the model.

A recording is a folder under data/recordings/<name>/ (gitignored, local only):
    recording.json   rows, extraction summary, every subject's result (with citations and excerpt text), timings, model, date, traces
    original.<ext>   the uploaded scan, so the verification screen can show it

Replayed output is never presented as fresh: every replayed result carries `replay.label`
("Replay of recorded run, <date>, <model>") and `is_replay: true`. Subjects chosen to run live are computed by the model now
and carry no replay label."""
import copy
import json
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import get_settings
from ..db.models import Application, Job, JobUnit, iso
from ..devlog import events
from ..runtime import rt

VERSION = 1
ROOT = Path(__file__).resolve().parents[3]


def recordings_dir() -> Path:
    return Path(get_settings().recordings_root).resolve()


def safe_name(name: str) -> str:
    n = re.sub(r"[^A-Za-z0-9_.-]", "_", name.strip())[:60].strip("._")
    if not n:
        raise ValueError("A recording needs a name")
    return n


def label(rec: dict[str, Any]) -> str:
    return f"Replay of recorded run, {rec['recorded_at'][:10]}, {rec['model']}"


def build(db, app_id: str, name: str) -> dict[str, Any]:
    app = db.get(Application, app_id)
    if app is None or app.kind != "upload":
        raise ValueError("Only an uploaded transcript can be recorded")
    if app.confirmed_at is None:
        raise ValueError("Only a run that reached the officer gate and was evaluated can be recorded")
    ev_job = db.get(Job, app.evaluation_job_id) if app.evaluation_job_id else None
    rd_job = db.scalar(select(Job).where(Job.app_id == app_id, Job.phase == "READING").order_by(Job.created_at.desc()))
    if ev_job is None or ev_job.status != "DONE":
        raise ValueError("The evaluation has not finished")
    units = list(db.scalars(select(JobUnit).where(JobUnit.app_id == app_id).order_by(JobUnit.stage, JobUnit.seq)))
    by_source = {r.get("source_id"): r for r in app.rows}
    started = rd_job.started_at.timestamp() if rd_job and rd_job.started_at else 0.0

    def timing(j: Job | None) -> dict[str, Any] | None:
        return j and {"job_id": j.id, "started_at": iso(j.started_at), "finished_at": iso(j.finished_at), "route": j.route, "model": j.model,
                      "total_s": round((j.finished_at - j.started_at).total_seconds(), 1) if j.started_at and j.finished_at else None}

    evaluation = []
    for u in units:
        if u.stage == "evaluate":
            row = by_source.get(u.unit_key) or {}
            evaluation.append({"row_id": row.get("id"), "title": row.get("title"), "unit_key": u.unit_key, "status": u.status, "seq": u.seq,
                               "duration_s": u.duration_s, "method": u.method, "error_code": u.error_code, "result": u.result})
    reading_units = [{"unit_key": u.unit_key, "status": u.status, "attempts": u.attempts, "method": u.method, "duration_s": u.duration_s,
                      "error_code": u.error_code} for u in units if u.stage == "read"]
    model = (ev_job.model or (rd_job.model if rd_job else "") or "unknown")
    return {
        "version": VERSION, "name": name, "recorded_at": datetime.now(UTC).isoformat(), "model": model,
        "provider": ev_job.route or "", "catalog": rt().repo.catalog_label,
        "prompt_versions": next((e["result"]["prompt_versions"] for e in evaluation if e["result"] and e["result"].get("prompt_versions")), {}),
        "source": {"filename": (app.document or {}).get("filename"), "sha256": (app.document or {}).get("sha256"),
                   "pages": (app.document or {}).get("page_count"), "ext": (app.document or {}).get("ext")},
        "reading": {"timing": timing(rd_job), "units": reading_units, "extraction": app.extraction},
        "rows": app.rows, "extracted_rows": app.extracted_rows, "corrections": app.corrections,
        "confirmed_by": app.confirmed_by, "confirmed_at": iso(app.confirmed_at),
        "evaluation": {"timing": timing(ev_job), "units": evaluation},
        "traces": [e for e in events.recent(2000) if e["ts"] >= started],  # tool calls and model calls of this run (document content already redacted)
    }


def save(db, app_id: str, name: str) -> dict[str, Any]:
    name = safe_name(name)
    rec = build(db, app_id, name)
    folder = recordings_dir() / name
    folder.mkdir(parents=True, exist_ok=True)
    app = db.get(Application, app_id)
    doc = app.document or {}
    if doc.get("path") and Path(doc["path"]).exists():
        shutil.copyfile(doc["path"], folder / f"original{doc.get('ext', '.pdf')}")
    (folder / "recording.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False, default=str))
    return summary(rec)


def summary(rec: dict[str, Any]) -> dict[str, Any]:
    subjects = [{"row_id": e["row_id"], "title": e["title"], "triage": (e["result"] or {}).get("triage", {}).get("category"),
                 "duration_s": e["duration_s"]} for e in rec["evaluation"]["units"] if e["status"] == "DONE" and e["result"]]
    return {"name": rec["name"], "recorded_at": rec["recorded_at"], "model": rec["model"], "label": label(rec), "subjects": subjects,
            "evaluation_s": (rec["evaluation"]["timing"] or {}).get("total_s"), "reading_s": (rec["reading"]["timing"] or {}).get("total_s")}


def load(name: str) -> dict[str, Any]:
    p = recordings_dir() / safe_name(name) / "recording.json"
    if not p.exists():
        raise FileNotFoundError(name)
    rec = json.loads(p.read_text())
    if rec.get("version") != VERSION:
        raise ValueError("Unsupported recording version")
    return rec


def listing() -> list[dict[str, Any]]:
    out = []
    for p in sorted(recordings_dir().glob("*/recording.json")) if recordings_dir().exists() else []:
        try:
            rec = json.loads(p.read_text())
            out.append(summary(rec))
        except (ValueError, KeyError):
            continue
    return out


def replayed_result(rec: dict[str, Any], entry: dict[str, Any], row: dict[str, Any], unit_key: str) -> dict[str, Any]:
    """A saved result adapted to the new application: new ids, labelled, never marked fresh. Content is untouched."""
    res = copy.deepcopy(entry["result"])
    res["result_id"] = uuid.uuid4().hex[:12]
    res["course"] = {**res["course"], "code": unit_key, "grade": row.get("grade") or res["course"].get("grade", ""),
                     "credits": str(row.get("credits") or res["course"].get("credits", ""))}
    res["is_replay"] = True
    res["replay"] = {"label": label(rec), "recorded_at": rec["recorded_at"], "model": rec["model"], "recorded_duration_s": entry["duration_s"],
                     "recorded_llm_calls": res.get("llm_calls")}
    return res


def can_replay(entry: dict[str, Any] | None, row: dict[str, Any], rec_row: dict[str, Any] | None) -> bool:
    """Replay only when the saved result still answers this row: same title and same linked curriculum subject."""
    if not entry or entry.get("status") != "DONE" or not entry.get("result") or not rec_row:
        return False
    same_subject = (row.get("subject") or {}).get("id") == (rec_row.get("subject") or {}).get("id")
    return same_subject and (row.get("title") or "").strip().lower() == (rec_row.get("title") or "").strip().lower()
