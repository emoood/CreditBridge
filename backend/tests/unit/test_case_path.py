"""Small tests for the code-first path: gate, mapping lookup, conditions, row state, the Case agent's guards and resume. Fake model only."""
import pytest
from sqlalchemy import delete, select

from backend.app import runtime
from backend.app.agents.case_agent import run_case_agent
from backend.app.agents.tools.base import ToolRegistry
from backend.app.agents.tools.case_tools import CASE_TOOLS, CaseContext
from backend.app.config import get_settings
from backend.app.db.models import CourseMapping, JobUnit, TraceEvent, UsageEntry
from backend.app.db.session import app_session
from backend.app.providers.base import ToolCall
from backend.app.providers.fake import FakeLLMProvider
from backend.app.services import mapping as mp
from backend.app.services.usage import MeteredProvider
from backend.tests.conftest import ADMIN, read_to_gate

pytestmark = pytest.mark.asyncio

TEST_MAPPINGS = [  # demo-catalog codes, so the policy check can run in tests
    {"subject_code": "AP-STAT", "aud_code": "STA201", "version": "t1", "status": "ACTIVE"},
    {"subject_code": "AP-PSYC", "aud_code": "MTH201", "version": "t1", "status": "ACTIVE", "condition": {"type": "min_grade", "value": "B"}},
    {"subject_code": "AP-CALC-AB", "aud_code": "MTH201", "version": "t1", "status": "ACTIVE", "condition": {"type": "min_exam_score", "value": 4, "evidence": "AP exam score report"}},
    {"subject_code": "AP-CSA", "aud_code": "CS101", "version": "t0", "status": "STALE"},
]


@pytest.fixture
def case_env(env, monkeypatch):
    monkeypatch.setattr(get_settings(), "case_agent", True)
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


async def test_gate_and_lookup_settle_rows_in_code_and_only_unsettled_rows_reach_the_model(case_env):
    app_id = await run_to_review(case_env)
    st, rows, res = await states(case_env, app_id)
    assert st["AP Statistics"]["status"] == "Mapped" and st["AP Psychology"]["status"] == "Mapped"   # no condition / condition met
    assert st["AP Calculus AB"]["status"] == "Needs you" and st["AP Calculus AB"]["kind"] == "condition_missing"
    assert st["AP Computer Science A"]["kind"] == "stale" and st["AP English Language and Composition"]["kind"] == "unmapped"
    assert st["Film Appreciation"]["status"] == "Not evaluated" and st["Film Appreciation"]["can_include"]  # not in the curriculum database
    sids = {r["title"]: r["source_id"] for r in rows}
    asked = {u for stage, u in case_env.model.calls if stage not in ("read",)}
    assert sids["AP Statistics"] not in asked and sids["AP Psychology"] not in asked and sids["Film Appreciation"] not in asked
    assert sids["AP Calculus AB"] in asked  # a Needs-you row did reach the Case agent (the scripted model cannot finish it: the row stays Needs you)
    mapped = next(r for r in res["results"] if r["course"]["title"] == "AP Statistics")
    assert mapped["match"]["status"] == "POTENTIAL_MATCH" and mapped["llm_calls"] == 0 and mapped["match"]["target"]["code"] == "STA201"


async def test_case_agent_drafts_a_request_pauses_the_row_and_evidence_resumes_only_that_row(case_env):
    app_id = await run_to_review(case_env)
    _st, rows, _res = await states(case_env, app_id)
    calc = next(r for r in rows if r["title"] == "AP Calculus AB")
    script = [[ToolCall("1", "read_mapping", {})],
              [ToolCall("2", "draft_request", {"what": "AP exam score report", "why": "The mapping needs a score of at least 4"})],
              [ToolCall("3", "set_row_state", {"status": "Waiting on document", "reason": "Waiting for the AP score report"})],
              [ToolCall("4", "submit_result", {"headline": "Needs the AP score report", "needs": "WAITING_ON_DOCUMENT", "missing_evidence": ["exam_score"]})]]
    fake = FakeLLMProvider(script)
    R = runtime.rt()
    out = await run_case_agent(MeteredProvider(fake, app_id, calc["id"]), R.repo, R.retriever, R.precedent, app_id, calc, calc["source_id"])
    assert out["status"] == "Waiting on document" and out["finding"]["missing_evidence"] == ["exam_score"]
    with app_session() as db:
        s = mp.get_state(db, app_id, calc["id"])
        assert s.status == "Waiting on document" and [q["status"] for q in s.requests] == ["DRAFT"]
        names = [e.name for e in db.scalars(select(TraceEvent).where(TraceEvent.row_id == calc["id"], TraceEvent.actor == "case_agent"))]
        assert names == ["read_mapping", "draft_request", "set_row_state"]  # every tool action is in the trace
        assert db.scalar(select(UsageEntry).where(UsageEntry.row_id == calc["id"], UsageEntry.stage == "case")) is not None  # and in the usage ledger
        unit_before = {u.unit_key: u.result["result_id"] for u in db.scalars(select(JobUnit).where(JobUnit.app_id == app_id, JobUnit.stage == "evaluate")) if u.result}
    n = len(case_env.model.calls)
    r = await case_env.client.post(f"/api/v1/applications/{app_id}/rows/{calc['id']}/evidence", data={"facts": '{"exam_score": 5}'}, headers=ADMIN)
    assert r.status_code == 200
    await case_env.worker.idle()
    assert len(case_env.model.calls) == n  # the condition is now met: settled by code, no model call
    with app_session() as db:
        s = mp.get_state(db, app_id, calc["id"])
        assert s.status == "Mapped" and [q["status"] for q in s.requests] == ["RECEIVED"] and s.facts == {"exam_score": 5}
        after = {u.unit_key: u.result["result_id"] for u in db.scalars(select(JobUnit).where(JobUnit.app_id == app_id, JobUnit.stage == "evaluate")) if u.result}
    changed = {k for k in after if after[k] != unit_before.get(k)}
    assert changed == {calc["source_id"]}  # only that row was re-worked


async def test_case_agent_cannot_mark_a_row_mapped_or_wait_without_a_request(case_env):
    app_id = await run_to_review(case_env)
    _st, rows, _ = await states(case_env, app_id)
    calc = next(r for r in rows if r["title"] == "AP Calculus AB")
    R = runtime.rt()
    ctx = CaseContext(R.repo, R.retriever, "case", calc["source_id"], app_id=app_id, row_id=calc["id"])
    reg = ToolRegistry(CASE_TOOLS)
    bad_wait = await reg.execute(ToolCall("1", "set_row_state", {"status": "Waiting on document", "reason": "no request drafted"}), ctx)
    bad_map = await reg.execute(ToolCall("2", "set_row_state", {"status": "Mapped", "reason": "I think it is fine"}), ctx)
    no_cands = await reg.execute(ToolCall("3", "propose_candidates", {}), ctx)  # this course has a mapping
    assert not bad_wait.ok and not bad_map.ok and not no_cands.ok


async def test_one_click_include_runs_a_not_evaluated_row_and_the_ledger_is_served(case_env):
    app_id = await run_to_review(case_env)
    st, rows, _ = await states(case_env, app_id)
    film = next(r for r in rows if r["title"] == "Film Appreciation")
    assert (await case_env.client.post(f"/api/v1/applications/{app_id}/rows/{film['id']}/include", headers={"X-Role": "registrar"})).status_code == 403
    assert (await case_env.client.post(f"/api/v1/applications/{app_id}/rows/{film['id']}/include", headers=ADMIN)).status_code == 200
    await case_env.worker.idle()
    st, _, _ = await states(case_env, app_id)
    assert st["Film Appreciation"]["status"] == "Needs you" and st["Film Appreciation"]["included_override"]
    act = (await case_env.client.get(f"/api/v1/applications/{app_id}/activity")).json()
    assert act["usage"]["calls"] > 0 and act["usage"]["cost_usd"] == 0 and any(e["name"] == "lookup" for e in act["trace"])
