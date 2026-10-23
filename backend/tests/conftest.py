"""Shared fixtures: in-memory SQLite for app state, a scripted provider, a fake curriculum, and the real worker/queue."""
import asyncio
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, delete
from sqlalchemy.pool import StaticPool

from backend.app import runtime
from backend.app.api.common import seed_demo
from backend.app.config import get_settings
from backend.app.db.models import CourseMapping
from backend.app.db.session import app_session, init_app_db, set_app_engine
from backend.app.main import app
from backend.app.providers.base import LLMRequest, LLMResponse, ToolCall
from backend.app.providers.guard import GuardedProvider
from backend.app.services.curriculum import Subject, curriculum
from backend.app.workers.queue import worker

FIX = Path(__file__).resolve().parents[2] / "fixtures" / "demo_v2"
SUBJECTS = [Subject("s-calc", "Calculus AB", "AP-CALC-AB", "Advanced Placement", "Advanced Placement", "College Board"),
            Subject("s-eng", "English Language and Composition", "AP-ENG-LANG", "Advanced Placement", "Advanced Placement", "College Board"),
            Subject("s-psy", "Psychology", "AP-PSYC", "Advanced Placement", "Advanced Placement", "College Board"),
            Subject("s-csa", "Computer Science A", "AP-CSA", "Advanced Placement", "Advanced Placement", "College Board"),
            Subject("s-stat", "Statistics", "AP-STAT", "Advanced Placement", "Advanced Placement", "College Board"),
            Subject("s-art", "Art History", "AP-ART-HIST", "Advanced Placement", "Advanced Placement", "College Board")]
OUTCOMES = [{"text": "Interpret derivatives as rates of change.", "page": 3, "kind": "objective"}]
FALL = [{"subject": "AP Calculus AB", "mark": 94, "letter": "A", "points": 4.0, "credits": 1.0},
        {"subject": "AP English Language and Composition", "mark": 88, "letter": "B+", "points": 3.3, "credits": 1.0},
        {"subject": "AP Psychology", "mark": 95, "letter": "A", "points": 4.0, "credits": 0.5},
        {"subject": "Film Appreciation", "mark": 90, "letter": "A-", "points": 3.7, "credits": 0.5}]
SPRING = [{"subject": "AP Computer Science A", "mark": 91, "letter": "A-", "points": 3.7, "credits": 1.0},
          {"subject": "AP Statistics", "mark": 85, "letter": "B", "points": 3.0, "credits": 1.0},
          {"subject": "AP Art History", "mark": 82, "letter": "B-", "points": 2.7, "credits": 0.5}]


class ScriptedModel:
    """Stands in for the model. Reads return canned blocks; agent calls return a valid NO_DEFENSIBLE_MATCH. Hooks inject
    hangs, errors and concurrency tracking. Clearly a TEST provider."""
    name, model = "fake:test", "fake"

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.read_script: list[list[dict]] = [FALL, SPRING]  # one entry per read call; later calls return no rows
        self.hang_for: set[str] = set()
        self.fail_for: set[str] = set()
        self.delay = 0.0
        self.agent_args: dict[str, dict] = {}  # per-unit canned submit_result arguments
        self.active = self.max_active = 0

    async def health(self):
        return None

    async def complete(self, request: LLMRequest) -> LLMResponse:
        unit = str(request.meta.get("course", ""))
        self.calls.append((request.stage, unit))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if unit in self.hang_for:
                await asyncio.sleep(3600)
            if unit in self.fail_for:
                raise RuntimeError("boom")
            if self.delay:
                await asyncio.sleep(self.delay)
            if request.stage == "read":
                n = sum(1 for s, _ in self.calls if s == "read") - 1
                rows = self.read_script[n] if n < len(self.read_script) else []
                return LLMResponse("", [ToolCall("1", "submit_block", {"rows": rows, **({"term_credits": 3.0} if rows is FALL else {})})], self.name, self.model, 0.0)
            return LLMResponse("", [ToolCall("1", "submit_result", self.agent_args.get(unit, {"recommendation": "NO_DEFENSIBLE_MATCH"}))], self.name, self.model, 0.0)
        finally:
            self.active -= 1


@pytest_asyncio.fixture
async def env(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    set_app_engine(engine)
    init_app_db()
    with app_session() as db:
        seed_demo(db)
    monkeypatch.setattr(get_settings(), "intake_agent", False)  # the older tests read with code only; the Intake agent has its own tests
    monkeypatch.setattr(get_settings(), "case_agent", False)  # the older tests exercise the model-evaluates-everything path; new tests turn this on
    monkeypatch.setattr(get_settings(), "catalog", "demo")  # tests use the small synthetic catalog, never the real one
    runtime.reset_runtime()
    model = ScriptedModel()
    runtime.provider_override = lambda: GuardedProvider(model, timeout=0.3, retries=1)
    monkeypatch.setattr(curriculum, "_subjects", SUBJECTS)
    monkeypatch.setattr(curriculum, "_at", time.time() + 10_000)
    monkeypatch.setattr(curriculum, "error", None)
    monkeypatch.setattr(curriculum, "outcomes", lambda sid: OUTCOMES)
    worker.queue = asyncio.Queue()
    worker.task = None
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    class Env:
        pass
    e = Env()
    e.model, e.client, e.worker = model, client, worker
    yield e
    await worker.stop()
    await client.aclose()
    runtime.provider_override = None
    set_app_engine(None)


ADMIN = {"X-Role": "admissions", "X-Actor": "Test Officer"}


async def upload(env, name="school_transcript.png"):
    data = (FIX / name).read_bytes()
    r = await env.client.post("/api/v1/applications", files={"file": (name, data, "image/png")}, headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


async def read_to_gate(env):
    up = await upload(env)
    env.worker.start()
    await env.worker.idle()
    return up


@pytest.fixture
def admin():
    return ADMIN


# ---- shared by the Case-agent path tests
TEST_MAPPINGS = [  # demo-catalog codes, so the policy check can run in tests
    {"subject_code": "AP-STAT", "aud_code": "STA201", "version": "t1", "status": "ACTIVE"},
    {"subject_code": "AP-PSYC", "aud_code": "MTH201", "version": "t1", "status": "ACTIVE", "condition": {"type": "min_grade", "value": "B"}},
    {"subject_code": "AP-CALC-AB", "aud_code": "MTH201", "version": "t1", "status": "ACTIVE", "condition": {"type": "min_exam_score", "value": 4, "evidence": "AP exam score report"}},
    {"subject_code": "AP-CSA", "aud_code": "CS101", "version": "t0", "status": "STALE"},
]


@pytest.fixture
def case_env(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "case_agent", True)
    monkeypatch.setattr(get_settings(), "case_review", False)  # the application-wide review has its own tests
    with app_session() as db:
        db.execute(delete(CourseMapping))
        db.add_all([CourseMapping(qualification="Advanced Placement", subject_name="", aud_credits=3, **m) for m in TEST_MAPPINGS])
        db.commit()
    return env


async def run_to_review(env):
    up = await read_to_gate(env)
    await env.client.post(f"/api/v1/applications/{up['id']}/confirm", headers=ADMIN)
    await env.worker.idle()
    return up["id"]


async def states(env, app_id):
    res = (await env.client.get(f"/api/v1/applications/{app_id}/results")).json()
    rows = (await env.client.get(f"/api/v1/applications/{app_id}")).json()["rows"]
    by_title = {r["title"]: res["states"][r["id"]] for r in rows}
    return by_title, rows, res


