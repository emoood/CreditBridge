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


async def test_auditor_proposes_a_rechecked_fix_with_a_code_built_explanation_and_the_officer_applies_it(case_env):
    app_id = await run_to_review(case_env)
    _, rows, _ = await states(case_env, app_id)
    rid = {r["title"]: r["id"] for r in rows}
    await case_env.client.post(f"/api/v1/applications/{app_id}/confirm-mapped", headers=ADMIN)
    with app_session() as db:
        db.scalar(select(CourseMapping).where(CourseMapping.subject_code == "AP-STAT")).aud_code = "CS102"
        db.commit()
    R = runtime.rt()
    script = [[ToolCall("1", "run_sheet_checks", {})],
              [ToolCall("2", "diff_sheet_vs_decisions", {})],
              [ToolCall("3", "propose_fix", {"row_id": rid["AP Statistics"], "aud_code": "CS102", "why": "The mapping record now says CS102"})],
              [ToolCall("4", "submit_result", {"summary": "I moved the sheet to the new mapping."})]]
    report = await run_auditor(FakeLLMProvider(script), R.repo, R.retriever, app_id)
    assert report["status"] == "OK" and report["passes_after"] is True   # the proposal fixes the copy of the sheet
    p = report["proposals"][0]
    assert p["touches_confirmed"] and p["from"]["aud_code"] == "STA201" and p["to"]["aud_code"] == "CS102" and p["rechecked"]   # shown as before and after
    assert "Check 'matches_mapping' fails" in report["explanations"][0]["explanation"] and "I moved" not in report["explanations"][0]["explanation"]  # built by code, not by the agent
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


def test_block_checks_ignore_headings_and_compare_a_total_with_the_whole_term_not_just_one_block():
    scale = gc.load_scale()
    heading = {"subject": "Subject", "mark": None, "letter": "null", "points": None, "credits": None}
    row = lambda s, c: {"subject": s, "mark": 90, "letter": "A-", "points": 3.7, "credits": c}
    b1 = {"rows": [heading, row("A", 1.0), row("B", 1.0)], "totals": {}}
    b2 = {"rows": [row("C", 0.5)], "totals": {"term_credits": 2.5, "term_quality_points": 9.25, "term_gpa": 3.7}}
    blocks = {"p1b1": b1, "p1b2": b2}
    assert xc.failures_in(blocks, "p1b1", scale) == []          # the heading row is not a course row
    assert xc.failures_in(blocks, "p1b2", scale) == []          # 1.0 + 1.0 + 0.5 = 2.5: the total matches the term, though this block alone has 0.5
    assert [f["check"] for f in xc.block_failures(b2, scale)] == ["credit_total", "grade_point_total", "gpa_total"] or xc.block_failures(b2, scale)  # alone it would raise a false alarm
    b2["totals"]["term_credits"] = 3.5
    assert [f["check"] for f in xc.failures_in(blocks, "p1b2", scale)] == ["credit_total"]   # a genuine mismatch is still caught


def test_planted_misread_changes_one_mark_before_the_checks_and_a_failed_block_can_be_accepted_only_if_all_rows_pass():
    from backend.app.agents.intake_agent import accept_failed_block
    from backend.app.services import vision_reader as vr
    scale = gc.load_scale()
    reading = vr.BlockReading.model_validate({"rows": [{"subject": "AP Chemistry", "mark": 85, "letter": "B", "points": 3.0, "credits": 1.0},
                                                       {"subject": "AP Biology", "mark": 89, "letter": "B+", "points": 3.3, "credits": 1.0}]})
    rec = vr.plant_misread(reading, {"subject": "AP Chemistry", "field": "mark", "wrong": 65})
    assert rec == {"subject": "AP Chemistry", "field": "mark", "from": 85, "to": 65.0} and reading.rows[1].mark == 89   # only that mark, nothing else
    checked = vr.check_block(reading, scale)
    assert checked["flags"] and xc.block_failures(checked, scale)[0]["field"] in ("grade", "points")                      # the planted value trips the grading-scale check
    assert vr.plant_misread(vr.BlockReading.model_validate({"rows": [{"subject": "Art", "mark": 80}]}), {"subject": "AP Chemistry", "wrong": 65}) is None
    good = vr.check_block(vr.BlockReading.model_validate({"rows": [{"subject": "AP Chemistry", "mark": 85, "letter": "B", "points": 3.0, "credits": 1.0}]}), scale)
    assert accept_failed_block(good, scale) is good and accept_failed_block(checked, scale) is None and accept_failed_block({"rows": []}, scale) is None


def test_a_failed_term_total_becomes_a_specific_question_on_each_row_of_the_term_and_an_unreadable_block_is_a_failure():
    scale = gc.load_scale()
    row = lambda s, c: {"subject": s, "mark": 90, "letter": "A-", "points": 3.7, "credits": c}
    blocks = {"p1b1": {"rows": [row("A", 1.0)], "totals": {}}, "p1b2": {"failed": True, "rows": [], "totals": {}, "error": "invalid_output"},
              "p1b3": {"rows": [row("B", 1.0)], "totals": {"term_credits": 7.5}}}
    cells = xc.cell_failures(blocks, scale)
    assert [(c["block"], c["check"]) for c in cells if c["check"] == "block_unreadable"] == [("p1b2", "block_unreadable")]
    qs = [(c["block"], c["row"], c["field"], xc.question_for(c)) for c in cells if c["check"] == "credit_total"]
    assert [(b, r, f) for b, r, f, _ in qs] == [("p1b1", 0, "credits"), ("p1b3", 0, "credits")]
    assert "7.5" in qs[0][3] and "2" in qs[0][3] and "'A'" in qs[0][3]


async def test_replay_of_a_case_agent_run_restores_everything_without_a_model_and_marks_it_recorded(case_env, monkeypatch, tmp_path):
    from backend.app.config import get_settings
    monkeypatch.setattr(get_settings(), "recordings_root", str(tmp_path))
    c = case_env.client
    app_id = await run_to_review(case_env)
    _, _, res = await states(case_env, app_id)
    await c.post(f"/api/v1/applications/{app_id}/confirm-mapped", headers=ADMIN)
    assert (await c.post("/api/v1/recordings", json={"app_id": app_id, "name": "t-case", "timings": {"total_wall_s": 1.5}}, headers=ADMIN)).status_code == 200
    listing = (await c.get("/api/v1/recordings")).json()[0]
    assert listing["case_path"] and listing["model_calls"] > 0 and listing["timings"] == {"total_wall_s": 1.5}
    before = len(case_env.model.calls)
    rp = (await c.post("/api/v1/recordings/t-case/replay", json={"live": []}, headers=ADMIN)).json()
    assert rp["replay"]["case_path"] and rp["status"] == "EXTRACTION_REVIEW_REQUIRED"
    await c.post(f"/api/v1/applications/{rp['id']}/confirm", headers=ADMIN)
    await case_env.worker.idle()
    assert len(case_env.model.calls) == before                                    # not one model call: every row came from the recording
    _, _, rres = await states(case_env, rp["id"])
    assert sorted(v["status"] for v in rres["states"].values()) == sorted(v["status"] for v in res["states"].values())   # same statuses as the recorded run
    assert len(rres["actions"]) == 2 and all(r["is_replay"] for r in rres["results"])                                  # the two confirmed rows came back as actions
    act = (await c.get(f"/api/v1/applications/{rp['id']}/activity")).json()
    assert act["usage"]["recorded_calls"] > 0 and act["usage"]["live_calls"] == 0 and act["trace"] and all(t["recorded"] for t in act["trace"] if t["actor"] != "code" or t["row_id"])
    assert (await c.get(f"/api/v1/applications/{rp['id']}/sheet")).json()["ok"]
    # one row chosen to run live: the model runs for that row only, and its entries are not marked as recorded
    live_title = "AP Calculus AB"
    rp2 = (await c.post("/api/v1/recordings/t-case/replay", json={"live": [live_title]}, headers=ADMIN)).json()
    await c.post(f"/api/v1/applications/{rp2['id']}/confirm", headers=ADMIN)
    await case_env.worker.idle()
    asked = {u for stage, u in case_env.model.calls[before:]}
    _, rows2, res2 = await states(case_env, rp2["id"])
    calc2 = next(r for r in rows2 if r["title"] == live_title)
    assert asked == {calc2["source_id"]}
    live_result = next(r for r in res2["results"] if r["course"]["title"] == live_title)
    assert not live_result["is_replay"] and sum(r["is_replay"] for r in res2["results"]) == len(res2["results"]) - 1


async def test_the_auditor_runs_on_a_clean_sheet_too_and_export_check_skips_a_sheet_already_audited_clean(case_env):
    app_id = await run_to_review(case_env)
    await case_env.client.post(f"/api/v1/applications/{app_id}/confirm-mapped", headers=ADMIN)
    first = (await case_env.client.post(f"/api/v1/applications/{app_id}/export-check", headers=ADMIN)).json()
    assert first["ready"] is False and first["job_id"]                      # no audit yet for this sheet: the Auditor runs
    case_env.model.agent_args["audit"] = {}
    await case_env.worker.idle()
    sheet = (await case_env.client.get(f"/api/v1/applications/{app_id}/sheet")).json()
    assert sheet["audit"]["clean"] is True and sheet["audit"]["proposals"] == [] and sheet["audit"]["sheet_hash"] == sheet["sheet_hash"]
    second = (await case_env.client.post(f"/api/v1/applications/{app_id}/export-check", headers=ADMIN)).json()
    assert second == {"ready": True}                                         # this exact sheet was audited clean
    r = await case_env.client.post(f"/api/v1/applications/{app_id}/rows/{(await states(case_env, app_id))[1][0]['id']}/action", json={"action": "REJECT", "reason": "x"}, headers=ADMIN)
    assert r.status_code == 200
    third = (await case_env.client.post(f"/api/v1/applications/{app_id}/export-check", headers=ADMIN)).json()
    assert third["ready"] is False                                           # the sheet changed: audited again


async def test_propose_fix_is_refused_without_a_failure_and_when_the_recheck_fails(case_env):
    app_id = await run_to_review(case_env)
    _, rows, _ = await states(case_env, app_id)
    rid = {r["title"]: r["id"] for r in rows}
    await case_env.client.post(f"/api/v1/applications/{app_id}/confirm-mapped", headers=ADMIN)
    from backend.app.agents.auditor_agent import AuditContext, AuditorRegistry, ProposeFix
    R = runtime.rt()
    ctx = AuditContext(R.repo, R.retriever, "auditor", app_id, app_id=app_id)
    reg = AuditorRegistry([ProposeFix()])
    clean_row = await reg.execute(ToolCall("1", "propose_fix", {"row_id": rid["AP Statistics"], "aud_code": "CS102", "why": "just because"}), ctx)
    assert not clean_row.ok and "nothing to fix" in clean_row.error
    with app_session() as db:
        db.scalar(select(CourseMapping).where(CourseMapping.subject_code == "AP-STAT")).aud_code = "CS102"
        db.commit()
    wrong = await reg.execute(ToolCall("2", "propose_fix", {"row_id": rid["AP Statistics"], "aud_code": "STA201", "why": "keep the old one"}), ctx)
    assert not wrong.ok and "does not hold" in wrong.error        # the record says CS102: the fix still fails the check
    right = await reg.execute(ToolCall("3", "propose_fix", {"row_id": rid["AP Statistics"], "aud_code": "CS102", "why": "match the record"}), ctx)
    assert right.ok and ctx.proposals[rid["AP Statistics"]]["rechecked"]


async def test_a_stale_mapping_can_be_reconfirmed_by_the_officer_and_still_passes_the_sheet_checks(case_env):
    app_id = await run_to_review(case_env)
    _, rows, _ = await states(case_env, app_id)
    csa = next(r for r in rows if r["title"] == "AP Computer Science A")
    r = await case_env.client.post(f"/api/v1/applications/{app_id}/rows/{csa['id']}/action", json={"action": "RECONFIRM"}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["chosen"]["aud_code"] == "CS101" and r.json()["original"]["kind"] == "stale"
    stat = next(x for x in rows if x["title"] == "AP Statistics")
    assert (await case_env.client.post(f"/api/v1/applications/{app_id}/rows/{stat['id']}/action", json={"action": "RECONFIRM"}, headers=ADMIN)).status_code == 409   # only a stale mapping
    sheet = (await case_env.client.get(f"/api/v1/applications/{app_id}/sheet")).json()
    assert sheet["ok"] and any(ln["row_id"] == csa["id"] and ln["aud_code"] == "CS101" and ln["status"] == "Confirmed" for ln in sheet["rows"])
