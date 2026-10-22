"""Small tests for the grade rule, the synthetic demo transcripts and the planted-for-demo copy error. No model is called."""
import json
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select

from backend.app.config import get_settings
from backend.app.db.models import Application, TraceEvent
from backend.app.db.session import app_session
from backend.app.services import actions as ac
from backend.app.services import extraction_checks as xc
from backend.app.services import grade_checks as gc
from backend.app.services import mapping as mp
from backend.app.services.extraction import extract_transcript
from backend.tests.conftest import ADMIN, run_to_review

V3 = Path(__file__).resolve().parents[3] / "fixtures" / "demo_v3"
COND = {"type": "min_grade", "value": "B"}


def test_min_grade_uses_the_letter_column_else_the_numeric_mark_through_the_printed_scale():
    printed = gc.scale_from_text("Grading scale: A 95-100 = 4.0 | B 85-94 = 3.0 | C 75-84 = 2.0 | D 65-74 = 1.0")
    assert printed and printed["source"] == "printed"
    assert mp.check_condition(COND, {"grade": "A-", "mark": 50}, {})["status"] == mp.MET            # the letter column wins over a contradicting mark
    assert mp.check_condition(COND, {"grade": "86", "mark": 86.0}, {}, printed)["status"] == mp.MET   # 86 is a B on THIS transcript's scale ...
    assert gc.letter_for_row({"grade": "86", "mark": 86.0}, None)[0] == "B"                          # ... and also B on the default scale
    assert mp.check_condition(COND, {"grade": "80", "mark": 80.0}, {}, printed)["status"] == mp.UNMET  # 80 is a C on the printed scale (default scale says B-)
    assert gc.letter_for_row({"grade": "80", "mark": 80.0}, None)[0] == "B-"
    assert "printed" in mp.check_condition(COND, {"grade": "90", "mark": 90.0}, {}, printed)["reason"]
    assert mp.check_condition(COND, {"grade": "", "mark": None}, {})["status"] == mp.MISSING          # nothing to go on


def test_text_layer_transcripts_parse_with_their_printed_scale_and_the_image_one_has_no_text_layer():
    exp = json.loads((V3 / "expected.json").read_text())
    for key, file in (("A_live_demo", "transcript_a_live_demo.pdf"), ("C_copy_error", "transcript_c_copy_error.pdf")):
        res = extract_transcript(str(V3 / file), "demo")
        assert res.method == "digital_pdf" and [(r.title, r.grade) for r in res.rows] == [(x["subject"], x["letter"]) for x in exp[key]["rows"]]
        assert res.scale and res.scale["source"] == "printed" and res.scale["bands"][0][:2] == [93, "A"]
    assert sum(len(p.get_text()) for p in pymupdf.open(V3 / "transcript_b_intake.pdf")) == 0       # image only
    b = exp["B_intake"]
    block = {"rows": [{"subject": r["subject"], "mark": r["mark"], "letter": r["letter"], "points": r["points"], "credits": r["credits"]} for r in b["rows"]],
             "totals": {"term_credits": b["term_totals"]["credits"], "term_quality_points": b["term_totals"]["quality_points"], "term_gpa": b["term_totals"]["gpa"]}}
    assert xc.block_failures(block, gc.load_scale()) == []                                         # what is PRINTED is consistent ...
    block["rows"][2]["mark"], block["rows"][2]["credits"] = 65, 0.5                                 # ... a misread of the smudged cells is caught
    assert {f["check"] for f in xc.block_failures(block, gc.load_scale())} >= {"grading_scale", "credit_total"}


@pytest.mark.asyncio
async def test_planted_copy_error_is_labelled_caught_by_the_normal_checks_and_cleared_by_a_correction(case_env):
    app_id = await run_to_review(case_env)
    post = case_env.client.post
    assert (await post(f"/api/v1/applications/{app_id}/demo/plant-copy-error", json={"on": True}, headers=ADMIN)).status_code == 409   # nothing confirmed yet
    await post(f"/api/v1/applications/{app_id}/confirm-mapped", headers=ADMIN)
    assert (await case_env.client.get(f"/api/v1/applications/{app_id}/sheet")).json()["ok"]
    planted = (await post(f"/api/v1/applications/{app_id}/demo/plant-copy-error", json={"on": True}, headers=ADMIN)).json()["planted"]
    assert planted["label"] == "planted for demo"
    sheet = (await case_env.client.get(f"/api/v1/applications/{app_id}/sheet")).json()
    assert [c["row_id"] for c in sheet["checks"]] == [planted["row_id"]] and "[planted for demo]" in sheet["checks"][0]["message"] and sheet["planted"]["to"] == planted["to"]
    assert any(ln.get("planted") for ln in sheet["rows"])
    with app_session() as db:
        assert db.scalar(select(TraceEvent).where(TraceEvent.name == "planted_for_demo")) is not None   # labelled in the activity trace
        row = next(r for r in db.get(Application, app_id).rows if r["id"] == planted["row_id"])
        ac.record(db, db.get(Application, app_id), row, "CORRECT", "officer", "admissions", planted["from"], 3, "Auditor proposal")
    assert (await case_env.client.get(f"/api/v1/applications/{app_id}/sheet")).json()["ok"]            # the correction supersedes the planted change
    assert (await post(f"/api/v1/applications/{app_id}/demo/plant-copy-error", json={"on": True}, headers={**ADMIN, "X-Role": "registrar"})).status_code == 403


@pytest.mark.asyncio
async def test_demo_switch_is_refused_when_demo_mode_is_off(case_env, monkeypatch):
    app_id = await run_to_review(case_env)
    monkeypatch.setattr(get_settings(), "demo_mode", False)
    assert (await case_env.client.post(f"/api/v1/applications/{app_id}/demo/plant-copy-error", json={"on": True}, headers=ADMIN)).status_code == 404
