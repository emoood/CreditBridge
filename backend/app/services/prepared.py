"""Prepared runs: a finished REAL run saved at the model boundary, and the provider that plays it back.

Capture (demo mode only, synthetic data): every model reply is stored under what the call was: its stage, a key for the agent run it belongs to (a hash of the run's
first user message, plus the block key for vision reads) and its position in that run. Playback answers the same calls from the file. The agents and every tool still
run live in code, so row statuses, program-aware mapping, sheet checks and officer actions are real; only the model's words are recorded. A call with no recorded
reply raises NoPreparedRun: nothing is ever invented."""
import asyncio
import hashlib
import json
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
VERSION = 1


class NoPreparedRun(Exception):
    """The prepared run has no recorded reply for this step."""


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
               "label": f"Prepared run, recorded {datetime.now(UTC).date().isoformat()}, {', '.join(models)}",
               "source": {"filename": doc.get("filename"), "sha256": doc.get("sha256")}, "planted": {"misread": bool(ex.get("planted_misread")), "copy_error": bool(ex.get("planted_copy_error"))},
               "links": {(r.get("title") or "").strip().lower(): r["subject"] for r in app.rows if r.get("subject")},
               "model_calls": len(log), "model_log": log}
    DIR.mkdir(parents=True, exist_ok=True)
    path_of(name).write_text(json.dumps(rec, indent=1, ensure_ascii=False, default=str))
    return summary(rec)


def summary(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: rec[k] for k in ("name", "case_number", "recorded_at", "model", "label", "source", "planted", "model_calls", "notes") if k in rec}


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
