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
    from backend.app.services import student_record as sr
    rec = sr.load_record("cb-2026-0102")
    assert rec["name"] == "Daniel Ferreira" and rec["program"] == "BSc Computer Engineering" and rec["term"] and rec["curriculum"]
    assert sr.load_record("nope") is None


def test_classification_is_blocked_then_core_then_general_elective():
    from backend.app.services import student_record as sr
    eng, bba = "BSc Computer Engineering", "BBA Business Administration"
    assert sr.classify("ACCG 201", eng)["value"] == sr.NOT_APPLICABLE and sr.classify("ACCG 201", eng)["why"] == "Business-only courses are not open to engineering programs."
    assert sr.classify("MATH 210", eng)["value"] == sr.PROGRAM == "Core course"
    assert sr.classify("ECON 201", eng)["value"] == sr.ELECTIVE  # any other mapped course is a potential general elective: no list, no cap
    assert sr.classify("ARTS 200", eng)["value"] == sr.ELECTIVE
    assert sr.classify("ENGG 140", bba)["value"] == sr.NOT_APPLICABLE and sr.classify("MATH 220", bba)["value"] == sr.NOT_APPLICABLE
    assert sr.classify("ECON 201", bba)["value"] == sr.PROGRAM
    assert sr.classify("MATH 210", "No such program") is None


# ---- the Case agent reviews every application (mapped rows included); code corroborates doubts
async def _run_with_program(env, program="BSc Computer Engineering"):
    from backend.app.db.models import Application
    from backend.tests.conftest import read_to_gate
    up = await read_to_gate(env)
    with app_session() as db:
        db.get(Application, up["id"]).program = program
        db.commit()
    await env.client.post(f"/api/v1/applications/{up['id']}/confirm", headers=ADMIN)
    await env.worker.idle()
    return up["id"]


async def test_mapped_rows_run_first_and_carry_a_program_class(case_env):
    app_id = await _run_with_program(case_env)
    st, rows, _res = await states(case_env, app_id)
    assert st["AP Statistics"]["classification"]["value"] == "General elective suggestion"  # STA201 is not a core course for engineering
    with app_session() as db:
        order = [u.unit_key for u in db.scalars(select(JobUnit).where(JobUnit.app_id == app_id, JobUnit.stage == "evaluate").order_by(JobUnit.id))]
    sid = {r["title"]: r["source_id"] for r in rows}
    mapped = {sid["AP Statistics"], sid["AP Psychology"]}
    assert set(order[:2]) == mapped  # mapped rows come first in the queue; the agent rows follow


async def test_raise_doubt_is_checked_by_code_and_blocks_bulk_confirm(case_env):
    app_id = await _run_with_program(case_env)
    _st, rows, _ = await states(case_env, app_id)
    R = runtime.rt()
    row = {r["title"]: r for r in rows}
    ctx = CaseContext(R.repo, R.retriever, "case", "", app_id=app_id, row_id="")
    reg = ToolRegistry(CASE_TOOLS)
    ok = await reg.execute(ToolCall("1", "raise_doubt", {"row_id": row["AP Statistics"]["id"], "kind": "near_miss_grade", "reason": "Grade is at the minimum"}), ctx)
    stale_on_fine = await reg.execute(ToolCall("2", "raise_doubt", {"row_id": row["AP Psychology"]["id"], "kind": "stale_mapping", "reason": "Looks old to me"}), ctx)
    stale = await reg.execute(ToolCall("3", "raise_doubt", {"row_id": row["AP Computer Science A"]["id"], "kind": "stale_mapping", "reason": "Version t0 is older than t1"}), ctx)
    other = await reg.execute(ToolCall("4", "raise_doubt", {"row_id": row["AP Psychology"]["id"], "kind": "other", "reason": "Grade looks odd for this course"}), ctx)
    assert not ok.ok and not stale_on_fine.ok and stale.ok and other.ok and other.data["verified_by_code"] is False
    r = await case_env.client.post(f"/api/v1/applications/{app_id}/confirm-mapped", headers=ADMIN)
    assert r.status_code == 200
    done = (await case_env.client.get(f"/api/v1/applications/{app_id}/results")).json()["actions"]
    assert row["AP Computer Science A"]["id"] not in done and row["AP Psychology"]["id"] not in done  # a doubted or open row is not in the bulk confirm


async def test_the_review_pass_writes_a_summary_and_logs_its_tool_calls(case_env, monkeypatch):
    from backend.app.config import get_settings
    monkeypatch.setattr(get_settings(), "case_review", True)
    case_env.model.agent_args[""] = {"headline": "Two rows are mapped and three need your input.", "needs": "NEEDS_YOU"}
    app_id = await _run_with_program(case_env)
    from backend.app.db.models import Application
    with app_session() as db:
        summary = db.get(Application, app_id).extraction["case_summary"]
    assert summary["text"].startswith("Two rows are mapped") and summary["by"] == "case_agent"
    act = (await case_env.client.get(f"/api/v1/applications/{app_id}/activity")).json()
    assert act["usage"]["by_stage"]["case_review"]["calls"] >= 1


# ---- a school synopsis page is the syllabus; a course missing from it needs its syllabus
def test_a_course_missing_from_the_schools_synopsis_waits_for_its_syllabus_and_a_document_satisfies_it():
    from backend.app.db.models import CourseMapping
    syn = {"AP Statistics": "Covers exploring data and probability."}
    m = CourseMapping(id=1, qualification="Advanced Placement", subject_code="AP-STAT", subject_name="", aud_code="STA201", aud_credits=3, version="t1", status="ACTIVE")
    from backend.app.services.demo_repo import DemoRepo
    repo = DemoRepo()
    row = lambda t: {"title": t, "grade": "A", "subject": {"code": "AP-STAT", "qualification": "Advanced Placement"}, "included": True}
    have = mp.decide_row(row("AP Statistics"), m, {}, False, repo, synopsis=syn)
    miss = mp.decide_row(row("AP Chemistry"), m, {}, False, repo, synopsis=syn)
    sent = mp.decide_row(row("AP Chemistry"), m, {"syllabus": True}, False, repo, synopsis=syn)
    none = mp.decide_row(row("AP Chemistry"), m, {}, False, repo, synopsis=None)
    assert have["status"] == "Mapped" and sent["status"] == "Mapped" and none["status"] == "Mapped"   # no synopsis page, no such rule
    assert miss["status"] == "Conditional" and miss["condition_result"]["missing"][0]["field"] == "syllabus"


# ---- Demo mode: a prepared run answers the model's calls; code stays live; repeated runs give the same result
async def _demo_upload(env, name="school_transcript.png", **extra):
    from backend.tests.conftest import FIX
    data = (FIX / name).read_bytes()
    return await env.client.post("/api/v1/applications", files={"file": (name, data, "image/png")}, headers=ADMIN, data={"demo": "1", "demo_speed": "instant", **extra})


async def test_demo_mode_plays_back_a_prepared_run_with_no_model_and_repeats_identically(case_env, monkeypatch, tmp_path):
    from backend.app.services import prepared
    monkeypatch.setattr(prepared, "DIR", tmp_path)
    app1 = await run_to_review(case_env)
    st1, _rows1, _ = await states(case_env, app1)
    prepared.save(app1, "t_run", "CB-2026-0101")
    assert [c["case_number"] for c in prepared.listing()] == ["CB-2026-0101"]
    calls_before = len(case_env.model.calls)
    out = []
    for _ in range(2):  # twice: the same file gives the same result both times
        r = await _demo_upload(case_env)
        assert r.status_code == 200, r.text
        app_id = r.json()["id"]
        await case_env.worker.idle()
        await case_env.client.post(f"/api/v1/applications/{app_id}/confirm", headers=ADMIN)
        await case_env.worker.idle()
        st, _rows, _res = await states(case_env, app_id)
        out.append({t: (s["status"], s["kind"]) for t, s in st.items()})
        act = (await case_env.client.get(f"/api/v1/applications/{app_id}/activity")).json()
        assert act["usage"]["live_calls"] == 0 and act["usage"]["recorded_calls"] > 0          # every model call came from the prepared run
        assert act["trace"] and all(e["recorded"] for e in act["trace"] if e["actor"] == "case_agent")
    assert len(case_env.model.calls) == calls_before                                            # the model was never called
    assert out[0] == out[1] == {t: (s["status"], s["kind"]) for t, s in st1.items()}              # same file, same result, same as the real run


async def test_demo_mode_says_so_when_there_is_no_prepared_run_for_a_file(case_env, monkeypatch, tmp_path):
    from backend.app.services import prepared
    monkeypatch.setattr(prepared, "DIR", tmp_path)
    r = await _demo_upload(case_env)
    assert r.status_code == 404 and r.json()["detail"] == "No prepared run for this file"


# ---- applicability: a course the program cannot take is Not applicable BEFORE conditions and stale checks
async def test_a_restricted_course_is_not_applicable_and_needs_no_document_or_agent(case_env, monkeypatch):
    from backend.app.services import student_record as sr
    monkeypatch.setattr(sr, "_constraints", lambda: {"t": {"names": ["BBA Test"], "blocked_areas": [], "blocked_courses": ["CS101"], "core_courses": [],
                                                           "reasons": {"blocked": "Engineering courses are not open to BBA programs."}}})
    app_id = await _run_with_program(case_env, "BBA Test")
    st, rows, _res = await states(case_env, app_id)
    csa = st["AP Computer Science A"]
    assert csa["status"] == "Not evaluated" and csa["kind"] == "not_applicable" and csa["can_include"]   # stale mapping to CS101, but applicability comes first
    assert csa["reason"] == "Engineering courses are not open to BBA programs." and csa["requests"] == []
    sid = {r["title"]: r["source_id"] for r in rows}
    assert sid["AP Computer Science A"] not in {u for stage, u in case_env.model.calls if stage == "case"}   # no agent investigation
    row_id = next(x["id"] for x in rows if x["title"] == "AP Computer Science A")
    assert (await case_env.client.post(f"/api/v1/applications/{app_id}/rows/{row_id}/include", headers=ADMIN)).status_code == 200   # the officer includes it anyway
    await case_env.worker.idle()
    st2, _r, _x = await states(case_env, app_id)
    assert st2["AP Computer Science A"]["status"] == "Conditional" and st2["AP Computer Science A"]["kind"] == "stale"   # now it goes through the normal checks


async def test_a_blocked_course_is_refused_as_a_change_and_in_the_picker(case_env, monkeypatch):
    from backend.app.services import student_record as sr
    monkeypatch.setattr(sr, "_constraints", lambda: {"t": {"names": ["BBA Test"], "blocked_areas": [], "blocked_courses": ["CS101"], "core_courses": [], "reasons": {"blocked": "Engineering courses are not open to BBA programs."}}})
    app_id = await _run_with_program(case_env, "BBA Test")
    _st, rows, _ = await states(case_env, app_id)
    stat = next(r for r in rows if r["title"] == "AP Statistics")
    r = await case_env.client.post(f"/api/v1/applications/{app_id}/rows/{stat['id']}/action", json={"action": "CHANGE", "aud_code": "CS101", "credits": 3, "reason": "Better fit"}, headers=ADMIN)
    assert r.status_code == 409 and "not open to BBA" in r.text


def test_synopsis_match_is_exact_on_the_normalized_name():
    from backend.app.services.mapping import synopsis_for
    syn = {"AP Statistics": "x" * 50, "Economics": "y" * 50}
    assert synopsis_for({"title": "Statistics"}, syn) and synopsis_for({"title": "ap  statistics"}, syn) is not None
    assert synopsis_for({"title": "AP Macroeconomics"}, syn) is None and synopsis_for({"title": "AP Human Geography"}, syn) is None
