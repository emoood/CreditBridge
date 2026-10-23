"""C1 step 2, the extraction header: "Institution / curriculum" filled by Intake and cross-checked by code, student type, major for a university transcript, and
what credits mean for each type. No model: the agent runs on the fake provider, the API on the test client."""
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from backend.app.agents.intake_agent import (
    ADDENDUM,
    IntakeContext,
    IntakeReport,
    build_header,
    run_intake_agent,
)
from backend.app.db.models import Application
from backend.app.db.session import app_session
from backend.app.providers.base import ToolCall
from backend.app.providers.fake import FakeLLMProvider
from backend.app.services import extraction_checks as xc
from backend.app.services import grade_checks as gc
from backend.app.services import vision_reader as vr
from backend.app.workers import pipeline  # noqa: F401  (imports the whole app so the settings exist)
from backend.tests.conftest import ADMIN, read_to_gate

ROOT = Path(__file__).resolve().parents[3]
A2 = str(ROOT / "fixtures" / "demo_v3" / "transcript_a2_batch.pdf")


# ---------------------------------------------------------------------------------------------------------------- pure helpers
@pytest.mark.parametrize("text,curriculum,kind", [
    ("AP", "AP", "school"), ("Advanced Placement (AP)", "AP", "school"), ("International Baccalaureate Diploma", "IB", "school"), ("IB", "IB", "school"),
    ("A-Level", "A Level", "school"), ("GCE A Levels", "A Level", "school"), ("Baccalauréat Français", "French Bac", "school"), ("French Bac", "French Bac", "school"),
    ("University of Wollongong in Dubai", None, "university"), ("American High School Diploma", None, None), ("ap", None, None), ("", None, None)])
def test_a_curriculum_is_normalised_and_the_student_type_follows_from_the_value(text, curriculum, kind):
    assert xc.curriculum_of(text) == curriculum and xc.student_type_of(text) == kind


def test_the_curriculum_is_compared_with_the_case_record_after_normalising_both():
    assert xc.curriculum_check("Advanced Placement", "AP")["status"] == "match"
    c = xc.curriculum_check("Advanced Placement", "IB")
    assert c["status"] == "mismatch" and c["record"] == "AP" and c["seen"] == "IB"
    assert xc.curriculum_check("Advanced Placement", None)["status"] == "unchecked" and xc.curriculum_check(None, "AP")["status"] == "unchecked"
    assert xc.curriculum_check("Advanced Placement", "American High School Diploma")["status"] == "unchecked"   # not a curriculum name: nothing to compare


def test_the_text_layer_header_is_read_from_the_lines_above_the_first_table_or_inferred_from_course_names():
    h = xc.header_from_text(A2, ["AP Calculus AB", "AP Biology", "Moral Education"])
    assert h["value"] == "AP" and h["basis"].startswith("inferred") and h["major"] is None     # the page prints no curriculum: inferred from the course names
    assert xc.header_from_text(A2, ["Mathematics", "Physics"])["value"] is None                    # nothing to go on: nothing is invented
    assert xc.header_from_text(str(ROOT / "fixtures" / "demo_v3" / "transcript_b_intake.png"), ["AP Calculus AB"])["value"] is None   # no text layer in an image


def test_credits_follow_the_student_type():
    rows = [{"id": "a", "credits": "1.0", "included": True}, {"id": "b", "credits": "", "included": True}, {"id": "c", "credits": "", "included": False}]
    uni = xc.credits_view("university", rows)
    assert uni == {"show": True, "required": True, "missing": ["b"]}                                # required: a left-out row is not asked about
    assert xc.credits_view("school", rows) == {"show": True, "required": False, "missing": []}      # printed on some rows: shown, never required
    assert xc.credits_view("school", [{"id": "a", "credits": ""}]) == {"show": False, "required": False, "missing": []}   # not printed: not needed and not shown
    assert xc.credits_view(None, rows)["missing"] == []


# ---------------------------------------------------------------------------------------------------------------- the agent
def ctx_for(tmp_path, report, path=None):
    img = Image.new("RGB", (900, 700), "white")
    d = ImageDraw.Draw(img)
    for y in list(range(30, 200, 25)):
        d.rectangle([40, y, 600, y + 10], fill="black")
    png = str(tmp_path / "t.png")
    img.save(png)
    spans = {f"p1b{i + 1}": (1, sp) for i, sp in enumerate(vr.split_blocks(img, 300))}
    rows = [{"subject": s, "mark": 85, "letter": "B", "points": 3.0, "credits": 1.0} for s in ("AP Chemistry", "AP Biology", "AP Calculus AB")]   # the inference needs 2+ AP names
    script = [[ToolCall("a", "read_block", {"targets": list(spans)})], [ToolCall("v", "submit_block", {"rows": rows})], [ToolCall("a", "submit_courses", {"blocks_read": list(spans), "note": "done", **report})]]
    return IntakeContext(None, None, "intake", "", app_id="APP-T", provider=FakeLLMProvider(script), path=path or png, scale=gc.load_scale(), spans=spans,
                         lines={k: vr.count_text_lines(img, sp) for k, (_p, sp) in spans.items()})


@pytest.mark.asyncio
async def test_the_agent_fills_the_header_and_code_stores_it_in_the_name_check_the_pipeline_keeps(tmp_path):
    ctx = ctx_for(tmp_path, {"institution_curriculum": "Advanced Placement (AP)", "major": "BS Finance"}, A2)
    await run_intake_agent(ctx)
    h = ctx.name_check["header"]
    assert h["value"] == "AP" and h["source"] == "agent (vision)" and h["text_layer"] == "AP" and "disagrees" not in h
    assert h["major"] is None                                                                      # a school transcript has no major, whatever the agent said
    assert ctx.name_check["status"] in ("unchecked", "match", "mismatch")                          # the name check is still there, untouched


@pytest.mark.asyncio
async def test_a_value_the_agent_did_not_give_is_filled_from_the_text_layer_and_says_so(tmp_path):
    ctx = ctx_for(tmp_path, {}, A2)
    await run_intake_agent(ctx)
    h = ctx.name_check["header"]
    assert h["value"] == "AP" and h["source"] == "text layer (code)" and h["basis"].startswith("inferred")


@pytest.mark.asyncio
async def test_when_the_agent_and_the_text_layer_disagree_the_agent_wins_and_the_difference_is_shown(tmp_path):
    ctx = ctx_for(tmp_path, {"institution_curriculum": "IB"}, A2)
    await run_intake_agent(ctx)
    h = ctx.name_check["header"]
    assert h["value"] == "IB" and h["disagrees"]["text_layer"] == "AP"


@pytest.mark.asyncio
async def test_nothing_is_invented_when_nothing_is_seen(tmp_path):
    ctx = ctx_for(tmp_path, {})
    await run_intake_agent(ctx)
    assert ctx.name_check["header"]["value"] is None and ctx.name_check["header"]["source"] is None


def test_a_university_transcript_takes_its_major_from_the_agent_then_the_text_layer_and_a_missing_major_stays_missing(tmp_path, monkeypatch):
    ctx = ctx_for(tmp_path, {})
    monkeypatch.setattr(xc, "header_from_text", lambda path, titles: {"value": "American University of Sharjah", "basis": "printed in the header", "major": "BSBA Finance"})
    ctx.submitted = {"institution_curriculum": "American University of Sharjah", "major": "Finance"}
    assert (build_header(ctx)["major"], build_header(ctx)["major_source"]) == ("Finance", "agent (vision)")
    ctx.submitted = {"institution_curriculum": "American University of Sharjah"}
    assert (build_header(ctx)["major"], build_header(ctx)["major_source"]) == ("BSBA Finance", "text layer (code)")
    monkeypatch.setattr(xc, "header_from_text", lambda path, titles: {"value": None, "basis": None, "major": None})
    assert build_header(ctx)["major"] is None


def test_the_agent_is_told_what_to_report_and_may_report_null():
    assert "institution_curriculum" in ADDENDUM and "major" in ADDENDUM and "never guess" in ADDENDUM
    r = IntakeReport(blocks_read=["p1b1"])
    assert r.institution_curriculum is None and r.major is None


# ---------------------------------------------------------------------------------------------------------------------- the API
async def at_gate(env, header=None, rows=None, student_record=None):
    up = await read_to_gate(env)
    with app_session() as db:
        app = db.get(Application, up["id"])
        ex = dict(app.extraction or {})
        ex["intake"] = {**(ex.get("intake") or {}), "name_check": {"status": "unchecked", **({"header": header} if header else {})}}
        if student_record:
            ex["student_record"] = student_record
        app.extraction = ex
        if rows is not None:
            app.rows = [{**app.rows[0], **r, "id": f"r{i}"} for i, r in enumerate(rows)]
        db.commit()
    return up["id"]


@pytest.mark.asyncio
async def test_the_detail_carries_the_header_student_type_and_the_comparison_with_the_case_record(env):
    app_id = await at_gate(env, {"value": "AP", "source": "agent (vision)", "text_layer": "AP", "major": None}, student_record={"curriculum": "Advanced Placement", "name": "X"})
    d = (await env.client.get(f"/api/v1/applications/{app_id}")).json()
    h = d["extraction"]["header"]
    assert h["value"] == "AP" and h["student_type"] == "school" and h["major"] is None and h["major_missing"] is False
    assert h["curriculum_check"] == {"status": "match", "record": "AP", "seen": "AP"} and h["source"] == "agent (vision)" and h["edited"] == {}
    assert d["extraction"]["credits"]["required"] is False and all(r["credits_missing"] is False for r in d["rows"])


@pytest.mark.asyncio
async def test_the_officer_edit_wins_is_attributed_and_moves_the_student_type_and_the_credit_rules(env):
    app_id = await at_gate(env, {"value": "AP", "source": "agent (vision)", "text_layer": "AP"}, rows=[{"credits": "3"}, {"credits": ""}])
    r = await env.client.patch(f"/api/v1/applications/{app_id}/header", json={"value": "University of Wollongong in Dubai"}, headers=ADMIN)
    assert r.status_code == 200
    h = r.json()
    assert h["value"] == "University of Wollongong in Dubai" and h["student_type"] == "university" and h["source"] == "officer"
    assert h["edited"]["value"]["by"] == "Test Officer" and h["edited"]["value"]["at"]
    assert h["major_missing"] is True and h["major"] is None and h["curriculum_check"]["status"] == "unchecked"   # a university transcript with no major: marked missing
    d = (await env.client.get(f"/api/v1/applications/{app_id}")).json()
    assert d["extraction"]["credits"] == {"show": True, "required": True, "missing": 1}
    assert [r["credits_missing"] for r in d["rows"]] == [False, True]                                  # "Credits missing" on the row with none
    h2 = (await env.client.patch(f"/api/v1/applications/{app_id}/header", json={"major": "Finance"}, headers=ADMIN)).json()
    assert h2["major"] == "Finance" and h2["major_missing"] is False and h2["edited"]["major"]["by"] == "Test Officer" and h2["edited"]["value"]["by"] == "Test Officer"


@pytest.mark.asyncio
async def test_a_blank_value_clears_the_header_and_only_the_fields_sent_change(env):
    app_id = await at_gate(env, {"value": "AP", "source": "agent (vision)"})
    h = (await env.client.patch(f"/api/v1/applications/{app_id}/header", json={"value": "  "}, headers=ADMIN)).json()
    assert h["value"] is None and h["student_type"] is None and "major" not in h["edited"]


@pytest.mark.asyncio
async def test_a_mismatch_with_the_case_record_is_reported(env):
    app_id = await at_gate(env, {"value": "IB", "source": "agent (vision)"}, student_record={"curriculum": "Advanced Placement", "name": "X"})
    h = (await env.client.get(f"/api/v1/applications/{app_id}")).json()["extraction"]["header"]
    assert h["curriculum_check"]["status"] == "mismatch" and h["curriculum_check"]["seen"] == "IB" and h["curriculum_check"]["record"] == "AP"


@pytest.mark.asyncio
async def test_the_header_cannot_be_edited_after_the_officer_confirmed(env):
    app_id = await at_gate(env, {"value": "AP"})
    assert (await env.client.post(f"/api/v1/applications/{app_id}/confirm", headers=ADMIN)).status_code == 200
    r = await env.client.patch(f"/api/v1/applications/{app_id}/header", json={"value": "IB"}, headers=ADMIN)
    assert r.status_code == 409
