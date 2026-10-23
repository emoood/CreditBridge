"""Small tests for the code-first path: gate, mapping lookup, conditions, row state, the Case agent's guards and resume. Fake model only."""
import pytest
from sqlalchemy import select

from backend.app import runtime
from backend.app.agents.case_agent import run_case_agent
from backend.app.agents.tools.base import ToolRegistry
from backend.app.agents.tools.case_tools import CASE_TOOLS, CaseContext
from backend.app.db.models import JobUnit, TraceEvent, UsageEntry
from backend.app.db.session import app_session
from backend.app.providers.base import ToolCall
from backend.app.providers.fake import FakeLLMProvider
from backend.app.services import mapping as mp
from backend.app.services.usage import MeteredProvider
from backend.tests.conftest import ADMIN, run_to_review, states

pytestmark = pytest.mark.asyncio

async def test_gate_and_lookup_settle_rows_in_code_and_only_unsettled_rows_reach_the_model(case_env):
    app_id = await run_to_review(case_env)
    st, rows, res = await states(case_env, app_id)
    assert st["AP Statistics"]["status"] == "Mapped" and st["AP Psychology"]["status"] == "Mapped"   # no condition / condition met
    assert st["AP Calculus AB"]["status"] == "Conditional" and st["AP Calculus AB"]["kind"] == "condition_missing"
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
    assert st["Film Appreciation"]["status"] == "No mapping" and st["Film Appreciation"]["included_override"]
    act = (await case_env.client.get(f"/api/v1/applications/{app_id}/activity")).json()
    assert act["usage"]["calls"] > 0 and act["usage"]["cost_usd"] == 0 and any(e["name"] == "lookup" for e in act["trace"])


# ---- program-aware mapping and the student record
def test_student_record_by_case_number():
    from app.services import student_record as sr
    rec = sr.load_record("cb-2026-0102")
    assert rec["name"] == "Daniel Ferreira" and rec["program"] == "BSc Computer Engineering" and rec["term"] and rec["curriculum"]
    assert sr.load_record("nope") is None


def test_classification_against_program_plan():
    from app.services import student_record as sr
    eng = "BSc Computer Engineering"
    assert sr.classify("MATH 210", eng)["value"] == sr.PROGRAM
    assert sr.classify("ECON 201", eng)["value"] == sr.ELECTIVE  # business course for an engineering student: elective suggestion
    assert sr.classify("QUAN 201", eng)["value"] == sr.NOT_APPLICABLE
    assert sr.classify("MATH 210", "No such program") is None
