"""Small tests for finishing a case: row actions, the checked sheet, final approve, the Auditor guard, the Intake recheck acceptance and notes. Fake model only."""
import pytest
from sqlalchemy import select

from backend.app import runtime
from backend.app.agents.auditor_agent import run_auditor
from backend.app.agents.intake_agent import accept_rechecked
from backend.app.db.models import CourseMapping, RowAction
from backend.app.db.session import app_session
from backend.app.providers.base import ToolCall
from backend.app.providers.fake import FakeLLMProvider
from backend.app.services import extraction_checks as xc
from backend.app.services import grade_checks as gc
from backend.app.services import mapping as mp
from backend.tests.conftest import ADMIN, run_to_review, states

pytestmark = pytest.mark.asyncio


async def act(env, app_id, row_id, **body):
    return await env.client.post(f"/api/v1/applications/{app_id}/rows/{row_id}/action", json=body, headers=ADMIN)


async def test_actions_need_reasons_store_the_original_suggestion_and_confirm_all_mapped_is_idempotent(case_env):
    app_id = await run_to_review(case_env)
    _, rows, _ = await states(case_env, app_id)
    rid = {r["title"]: r["id"] for r in rows}
    stats, calc, eng = rid["AP Statistics"], rid["AP Calculus AB"], rid["AP English Language and Composition"]
    assert (await act(case_env, app_id, calc, action="CONFIRM")).status_code == 409            # Conditional: nothing mapped to confirm
    assert (await act(case_env, app_id, eng, action="NO_CREDIT")).status_code == 422             # reason required
    assert (await act(case_env, app_id, stats, action="CHANGE", aud_code="CS101")).status_code == 422
    assert (await act(case_env, app_id, stats, action="CHANGE", aud_code="STA201", credits=3)).status_code == 422  # nothing changed
    r = await act(case_env, app_id, stats, action="CHANGE", aud_code="CS101", credits=4, reason="Student took the lab version")
    assert r.status_code == 200 and r.json()["original"]["aud_code"] == "STA201" and r.json()["chosen"] == {"aud_code": "CS101", "credits": 4} and r.json()["actor"]
    assert (await act(case_env, app_id, eng, action="NO_CREDIT", reason="No equivalent at AUD")).status_code == 200
    first = (await case_env.client.post(f"/api/v1/applications/{app_id}/confirm-mapped", headers=ADMIN)).json()
    again = (await case_env.client.post(f"/api/v1/applications/{app_id}/confirm-mapped", headers=ADMIN)).json()
    assert first["confirmed"] == 1 and again["confirmed"] == 0   # only AP Psychology was left Mapped and unresolved
    with app_session() as db:
        assert [a.batch for a in db.scalars(select(RowAction).where(RowAction.action == "CONFIRM"))] == [True]


async def test_sheet_checks_catch_a_changed_mapping_and_duplicates_and_final_approve_waits_for_every_relevant_row(case_env):
    app_id = await run_to_review(case_env)
    _, rows, _ = await states(case_env, app_id)
    rid = {r["title"]: r["id"] for r in rows}
    await case_env.client.post(f"/api/v1/applications/{app_id}/confirm-mapped", headers=ADMIN)
    assert (await case_env.client.post(f"/api/v1/applications/{app_id}/approve", headers=ADMIN)).status_code == 409   # unresolved rows remain
    sheet = (await case_env.client.get(f"/api/v1/applications/{app_id}/sheet")).json()
    assert sheet["ok"] and [h for h in sheet["headers"]] == ["Source course", "Grade", "AUD equivalent", "AUD credits", "Status"]
    assert any(ln["status"].startswith("Unconfirmed") for ln in sheet["rows"])
    with app_session() as db:  # the mapping record changes after the officer confirmed
        m = db.scalar(select(CourseMapping).where(CourseMapping.subject_code == "AP-STAT"))
        m.aud_code = "CS102"
        db.commit()
    bad = (await case_env.client.get(f"/api/v1/applications/{app_id}/sheet")).json()
    assert [(c["check"], c["row_id"]) for c in bad["checks"]] == [("matches_mapping", rid["AP Statistics"])]  # names the exact row
    assert (await case_env.client.post(f"/api/v1/applications/{app_id}/audit", headers=ADMIN)).status_code == 200     # a failing sheet may call the Auditor
    await case_env.worker.idle()


async def test_auditor_only_for_a_failing_sheet_proposes_and_the_officer_applies_with_the_original_kept(case_env):
    app_id = await run_to_review(case_env)
    _, rows, _ = await states(case_env, app_id)
    rid = {r["title"]: r["id"] for r in rows}
    assert (await case_env.client.post(f"/api/v1/applications/{app_id}/audit", headers=ADMIN)).status_code == 409    # passing sheet: no model
    await case_env.client.post(f"/api/v1/applications/{app_id}/confirm-mapped", headers=ADMIN)
    with app_session() as db:
        db.scalar(select(CourseMapping).where(CourseMapping.subject_code == "AP-STAT")).aud_code = "CS102"
        db.commit()
    R = runtime.rt()
    script = [[ToolCall("1", "list_failures", {})],
              [ToolCall("2", "propose_correction", {"row_id": rid["AP Statistics"], "aud_code": "CS102", "why": "The mapping record now says CS102"})],
              [ToolCall("3", "rerun_checks", {})],
              [ToolCall("4", "submit_result", {"summary": "Mapping moved to CS102", "explanations": [{"row_id": rid["AP Statistics"], "explanation": "Sheet had STA201, record says CS102"}]})]]
    report = await run_auditor(FakeLLMProvider(script), R.repo, R.retriever, app_id)
    assert report["status"] == "OK" and report["passes_after"] is True   # the proposal fixes the copy of the sheet
    p = report["proposals"][0]
    assert p["touches_confirmed"] and p["from"]["aud_code"] == "STA201" and p["to"]["aud_code"] == "CS102"   # shown as before and after
    with app_session() as db:  # nothing changed yet
        assert db.scalar(select(RowAction).where(RowAction.action == "CORRECT")) is None


def test_intake_recheck_accepts_a_corrected_value_only_when_its_check_passes():
    scale = gc.load_scale()
    old = {"rows": [{"subject": "Geography", "mark": 100, "letter": "B", "points": 4.0, "credits": 0.5}, {"subject": "Art", "mark": 80, "letter": "B-", "points": 2.7, "credits": 1.0}],
           "flags": [], "totals": {}}
    assert [f["field"] for f in xc.block_failures(old, scale)] == ["grade"]
    fixed = {"rows": [{**old["rows"][0], "letter": "A"}, old["rows"][1]], "totals": {}}
    merged, changes = accept_rechecked(old, fixed, scale)
    assert merged["rows"][0]["letter"] == "A" and changes == [{"row": "Geography", "field": "letter", "from": "B", "to": "A"}] and not xc.block_failures(merged, scale)
    still_wrong = {"rows": [{**old["rows"][0], "letter": "C"}, old["rows"][1]], "totals": {}}
    kept, none = accept_rechecked(old, still_wrong, scale)
    assert kept["rows"][0]["letter"] == "B" and none == []   # the new value fails the check too: not accepted


async def test_a_note_hands_the_row_back_and_is_stored_as_evidence(case_env):
    app_id = await run_to_review(case_env)
    _, rows, _ = await states(case_env, app_id)
    eng = next(r for r in rows if r["title"] == "AP English Language and Composition")
    r = await case_env.client.post(f"/api/v1/applications/{app_id}/rows/{eng['id']}/evidence", data={"note": "Student says this was the literature variant"}, headers=ADMIN)
    assert r.status_code == 200
    await case_env.worker.idle()
    with app_session() as db:
        s = mp.get_state(db, app_id, eng["id"])
        assert s.facts["_notes"][0]["text"].startswith("Student says") and s.status == "No mapping"
