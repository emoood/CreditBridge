"""Prepared runs: a finished REAL run saved at the model boundary, and the provider that plays it back.

Capture (demo mode only, synthetic data): every model reply is stored under what the call was: its stage, a key for the agent run it belongs to (a hash of the run's
first user message, plus the block key for vision reads) and its position in that run. Playback answers the same calls from the file. The agents and every tool still
run live in code, so row statuses, program-aware mapping, sheet checks and officer actions are real; only the model's words are recorded. A call with no recorded
reply raises NoPreparedRun: nothing is ever invented."""
import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import get_settings
from ..db.models import Application, ModelLog
from ..db.session import app_session
from ..providers.base import LLMRequest, LLMResponse, ToolCall

ROOT = Path(__file__).resolve().parents[3]
DIR = ROOT / "fixtures" / "prepared_runs"
DATA_DIR = ROOT / "policies" / "data"
VERSION = 1
TEXT_SUFFIXES = {".csv", ".yaml", ".yml", ".json", ".md", ".txt"}
VARIANTS = ("clean", "copy_error", "misread")  # a2c is the copy-error variant, a2m the planted misread; each planted-error switch is its own named run
NAME_RE = re.compile(r"^demo_ap_[a-z0-9]+_(clean|copy_error|misread)$")


class NoPreparedRun(Exception):
    """The prepared run has no recorded reply for this step."""


class RecordingOutOfDate(NoPreparedRun):
    """The recording was made against other data or another page reading than the ones in the code now, so it must not be played back."""


def run_name(student: str, variant: str) -> str:
    """demo_ap_<student>_<variant>: the first name in lower case, and clean, copy_error or misread."""
    first = re.sub(r"[^a-z0-9]", "", (student.split() or [""])[0].lower())
    if not first or variant not in VARIANTS:
        raise ValueError("a prepared run is named demo_ap_<student>_<clean|copy_error|misread>")
    return f"demo_ap_{first}_{variant}"


def data_version() -> str:
    """A hash of everything under policies/data/ (the AP mapping, the mapping conditions, the programme plans, the gen-ed categories). Line endings are normalised, so a
    checkout on Windows gives the same value. Changing any of those files after a recording makes the recording out of date."""
    h = hashlib.sha256()
    files = sorted(p for p in DATA_DIR.rglob("*") if p.is_file() and not p.name.startswith(".") and not p.name.endswith("~")) if DATA_DIR.exists() else []
    for p in files:
        b = p.read_bytes()
        if p.suffix.lower() in TEXT_SUFFIXES:
            b = b.replace(b"\r\n", b"\n")
        h.update(p.relative_to(DATA_DIR).as_posix().encode() + b"\0" + b + b"\0")
    return h.hexdigest()[:16]


def reading_version() -> str:
    """How a page is read: the Intake prompt version and the block layout. Recorded replies are keyed on block keys and on the agent's first message, so a recording made
    with another reading would not line up with the calls the code makes now."""
    from ..agents.intake_agent import PROMPT_VERSION
    from .vision_reader import LAYOUT_VERSION
    return f"{PROMPT_VERSION}+layout{LAYOUT_VERSION}"


def stale_reason(rec: dict[str, Any]) -> str | None:
    """Why a recording cannot be played back now, or None."""
    if not rec.get("data_version") or not rec.get("reading_version"):
        return "it was recorded before data versions were tracked"
    if rec["data_version"] != data_version():
        return "the mapping, condition or plan files in policies/data changed after it was recorded"
    if rec["reading_version"] != reading_version():
        return "the page reading (block layout or Intake prompt) changed after it was recorded"
    return None


def refusal(rec: dict[str, Any]) -> str | None:
    """The message shown instead of a replay when the recording is out of date."""
    why = stale_reason(rec)
    return f"Recording out of date: {why}. Re-record it, or switch to Live AI." if why else None


def call_key(request: LLMRequest) -> tuple[str, str, int]:
    """(stage, run key, turn). The run key is the same for every call of one agent run; the turn is how many assistant messages came before."""
    first_user = next((m.get("content", "") for m in request.messages if m.get("role") == "user"), "")
    extra = str(request.meta.get("course", "")) if request.stage == "read" else ""  # vision reads are one-shot calls: told apart by their block
    runkey = hashlib.sha1(f"{extra}|{first_user}".encode()).hexdigest()[:16]
    return request.stage, runkey, sum(1 for m in request.messages if m.get("role") == "assistant")


def _dump(resp: LLMResponse) -> dict[str, Any]:
    return {"content": resp.content, "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in resp.tool_calls], "provider": resp.provider, "model": resp.model,
            "duration_s": resp.duration_s, "prompt_tokens": resp.prompt_tokens, "output_tokens": resp.output_tokens}


def capture(app_id: str, request: LLMRequest, resp: LLMResponse) -> None:
    stage, runkey, turn = call_key(request)
    with app_session() as db:
        idx = db.query(ModelLog).filter_by(app_id=app_id, stage=stage, runkey=runkey).count() if stage == "read" else turn
        db.add(ModelLog(app_id=app_id, stage=stage, runkey=runkey, idx=idx, response=_dump(resp)))
        db.commit()


def path_of(name: str) -> Path:
    return DIR / f"{name}.json"


def save(app_id: str, name: str, case_number: str, notes: str = "") -> dict[str, Any]:
    """Write the finished run of this application as fixtures/prepared_runs/<name>.json (synthetic data only)."""
    with app_session() as db:
        app = db.get(Application, app_id)
        log = [{"stage": m.stage, "runkey": m.runkey, "idx": m.idx, "response": m.response} for m in db.scalars(select(ModelLog).where(ModelLog.app_id == app_id).order_by(ModelLog.id))]
        if not log:
            raise ValueError("Nothing was captured for this application (demo mode must be on while the real run happens)")
        doc = app.document or {}
        ex = app.extraction or {}
        models = sorted({e["response"].get("model", "") for e in log if e["response"].get("model")})
        rec = {"version": VERSION, "name": name, "case_number": case_number, "recorded_at": datetime.now(UTC).isoformat(), "model": ", ".join(models), "notes": notes,
               "data_version": data_version(), "reading_version": reading_version(),
               "label": f"Prepared run, recorded {datetime.now(UTC).date().isoformat()}, {', '.join(models)}",
               "source": {"filename": doc.get("filename"), "sha256": doc.get("sha256")}, "planted": {"misread": bool(ex.get("planted_misread")), "copy_error": bool(ex.get("planted_copy_error"))},
               "links": {(r.get("title") or "").strip().lower(): r["subject"] for r in app.rows if r.get("subject")},
               "model_calls": len(log), "model_log": log}
    DIR.mkdir(parents=True, exist_ok=True)
    path_of(name).write_text(json.dumps(rec, indent=1, ensure_ascii=False, default=str))
    return summary(rec)


def summary(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: rec[k] for k in ("name", "case_number", "recorded_at", "model", "label", "source", "planted", "model_calls", "notes", "data_version", "reading_version") if k in rec}


def load(name: str) -> dict[str, Any]:
    p = path_of(name)
    if not p.exists():
        raise NoPreparedRun(f"No prepared run named {name}")
    return json.loads(p.read_text())


def listing() -> list[dict[str, Any]]:
    out = []
    for p in sorted(DIR.glob("*.json")) if DIR.exists() else []:
        try:
            out.append(summary(json.loads(p.read_text())))
        except (ValueError, KeyError):
            continue
    return out


def variant_of(r: dict[str, Any]) -> str:
    return "misread" if r["planted"].get("misread") else "copy_error" if r["planted"].get("copy_error") else "clean"


def find(sha256: str, filename: str, misread: bool = False, name: str = "") -> dict[str, Any] | None:
    """The prepared run for an uploaded file: a run picked by name (it must be for this file), else the same file contents (by hash), then the same filename.
    The misread and copy-error variants are separate prepared runs; without a name only the clean run (or the misread one, when asked) is chosen."""
    if name:
        r = next((r for r in listing() if r["name"] == name), None)
        return r if r and (r["source"]["sha256"] == sha256 or r["source"]["filename"] == filename) else None
    runs = [r for r in listing() if variant_of(r) == ("misread" if misread else "clean")]
    return next((r for r in runs if r["source"]["sha256"] == sha256), None) or next((r for r in runs if r["source"]["filename"] == filename), None)


class DemoProvider:
    """Answers model calls from a prepared run. Each agent run (and the first vision read) waits `delay` seconds once, so the stages appear one after another."""
    is_demo = True

    def __init__(self, rec: dict[str, Any], delay: float = 0.5):
        if (msg := refusal(rec)):
            raise RecordingOutOfDate(msg)  # never play back a recording made against other data: refuse, do not mis-replay
        self.rec, self.delay = rec, delay
        self.name, self.model = "recorded:" + (rec["model"] or "model"), rec["model"]
        self.table = {(e["stage"], e["runkey"], e["idx"]): e["response"] for e in rec["model_log"]}
        self.reads: dict[tuple[str, str], int] = {}
        self.waited: set[str] = set()

    async def health(self):
        from ..providers.base import ProviderHealth
        return ProviderHealth(True, self.name, self.model, "prepared run (no model is running)")

    async def complete(self, request: LLMRequest) -> LLMResponse:
        stage, runkey, turn = call_key(request)
        if stage == "read":
            idx = self.reads.get((stage, runkey), 0)
            self.reads[(stage, runkey)] = idx + 1
        else:
            idx = turn
        r = self.table.get((stage, runkey, idx))
        if r is None:
            raise NoPreparedRun(f"No prepared run for this step ({stage})")
        if self.delay and (stage == "read" and "read" not in self.waited or stage != "read" and turn == 0):
            self.waited.add(stage)
            await asyncio.sleep(self.delay)
        return LLMResponse(r["content"], [ToolCall(c["id"], c["name"], c["arguments"]) for c in r["tool_calls"]], "recorded:" + r["provider"], r["model"], r["duration_s"],
                           r.get("prompt_tokens"), r.get("output_tokens"))


def delay_for(speed: str) -> float:
    return 0.0 if speed == "instant" else get_settings().demo_stage_seconds
