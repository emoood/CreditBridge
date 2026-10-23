"""The Intake agent drives the reading; code is its tools and verifier. Fake model only: the script alternates agent turns and the vision reads they trigger."""
import pytest
from PIL import Image, ImageDraw

from backend.app.agents.intake_agent import IntakeContext, run_intake_agent
from backend.app.providers.base import ToolCall
from backend.app.providers.fake import FakeLLMProvider
from backend.app.services import grade_checks as gc
from backend.app.services import vision_reader as vr
from backend.app.workers import pipeline  # noqa: F401  (imports the whole app so the settings exist)

pytestmark = pytest.mark.asyncio

GOOD = [{"subject": "AP Chemistry", "mark": 85, "letter": "B", "points": 3.0, "credits": 1.0}, {"subject": "AP Biology", "mark": 89, "letter": "B+", "points": 3.3, "credits": 1.0}]
SECOND = [{"subject": "AP Statistics", "mark": 96, "letter": "A", "points": 4.0, "credits": 1.0}]


def make_ctx(tmp_path, script, plant=None, text=None, budget=2):
    img = Image.new("RGB", (900, 700), "white")
    d = ImageDraw.Draw(img)
    for y in list(range(30, 200, 25)) + list(range(420, 600, 25)):  # two clusters of text lines with a gap between them: two blocks
        d.rectangle([40, y, 600, y + 10], fill="black")
    path = str(tmp_path / "t.png")
    img.save(path)
    spans = {f"p1b{i + 1}": (1, sp) for i, sp in enumerate(vr.split_blocks(img, 300))}
    assert len(spans) == 2
    return IntakeContext(None, None, "intake", "", app_id="APP-T", provider=FakeLLMProvider(script), path=path, scale=gc.load_scale(), spans=spans, plant=plant, text=text, recovery_left=budget, lines={k: vr.count_text_lines(img, sp) for k, (_p, sp) in spans.items()})


def read(rows, **tot):
    return [ToolCall("v", "submit_block", {"rows": rows, **tot})]


def tc(name, **args):
    return [ToolCall("a", name, args)]


FINISH = tc("submit_courses", blocks_read=["p1b1", "p1b2"], note="done")


async def test_the_agent_plans_reads_and_a_planted_misread_is_repaired_by_a_recovery_read_that_code_accepts(tmp_path):
    script = [tc("list_pages"), tc("read_block", targets=["p1b1", "p1b2"]), read(GOOD), read(SECOND),
              tc("run_checks"), tc("crop_region", block="p1b1", top=0.0, bottom=1.0, zoom=2.0), tc("read_block", targets=["p1b1.r1"], focus="the mark of AP Chemistry"), read(GOOD),
              tc("run_checks"), FINISH]
    ctx = make_ctx(tmp_path, script, plant={"subject": "AP Chemistry", "field": "mark", "wrong": 65})
    await run_intake_agent(ctx)
    chem = ctx.blocks["p1b1"]["rows"][0]
    assert chem["mark"] == 85 and ctx.planted and ctx.planted[0]["to"] == 65.0         # the planted value was replaced by a re-read that passes its check
    assert ctx.changes and ctx.changes[0]["field"] == "mark" and ctx.recovery_used == 1 and not ctx.provider.script
    assert not [f for f in ctx.blocks["p1b1"]["rows"] if f.get("questions")]               # nothing left to ask the officer


async def test_an_empty_reading_is_a_failed_read_and_a_recovery_read_fixes_it_only_when_every_row_passes(tmp_path):
    script = [tc("read_block", targets=["p1b1", "p1b2"]), read([]), read(SECOND), tc("run_checks"),
              tc("read_block", targets=["p1b1"]), read([{**GOOD[0], "letter": "A"}]),            # still wrong: not accepted
              tc("read_block", targets=["p1b1"]), read(GOOD), tc("run_checks"), FINISH]
    ctx = make_ctx(tmp_path, script)
    await run_intake_agent(ctx)
    assert ctx.blocks["p1b1"]["rows"] == [r for r in ctx.blocks["p1b1"]["rows"]] and len(ctx.blocks["p1b1"]["rows"]) == 2 and not ctx.blocks["p1b1"].get("failed")
    assert ctx.recovery_used == 2 and ctx.recovery_left == 0


async def test_recovery_rounds_are_limited_and_a_block_with_no_failure_cannot_be_re_read(tmp_path):
    script = [tc("read_block", targets=["p1b1", "p1b2"]), read(GOOD), read(SECOND), tc("read_block", targets=["p1b2"]), FINISH]
    ctx = make_ctx(tmp_path, script)
    await run_intake_agent(ctx)
    assert ctx.recovery_used == 0                      # nothing failed, so the re-read request was refused
    ctx2 = make_ctx(tmp_path, [tc("read_block", targets=["p1b1", "p1b2"]), read([{**GOOD[0], "letter": "A"}]), read(SECOND),
                               tc("read_block", targets=["p1b1"]), read([{**GOOD[0], "letter": "A"}]), tc("read_block", targets=["p1b1"]), read([{**GOOD[0], "letter": "A"}]),
                               tc("read_block", targets=["p1b1"]), FINISH], budget=2)
    await run_intake_agent(ctx2)
    assert ctx2.recovery_used == 2 and ctx2.provider.script == []
    assert ctx2.blocks["p1b1"]["rows"][0]["questions"][0]["field"] == "grade"   # unresolved: one specific question on the exact cell, written by code


async def test_flag_cell_accepts_only_failing_cells_for_check_failures_and_says_whether_code_corroborates_an_observation(tmp_path):
    dup = [GOOD[0], {**GOOD[0]}]
    script = [tc("read_block", targets=["p1b1", "p1b2"]), read(dup), read([{**SECOND[0], "letter": "B"}]), tc("run_checks"),
              tc("flag_cell", block="p1b1", row=0, field="grade", question="Is the grade right?"),             # row 0 passes its checks: refused
              tc("flag_cell", block="p1b1", row=1, field="title", question="Is this course on the transcript twice?", kind="repeated_course"),
              tc("flag_cell", block="p1b2", row=0, field="letter", question="Is the grade A or B for mark 96?"),   # alias letter -> grade, and it really fails
              FINISH]
    ctx = make_ctx(tmp_path, script)
    await run_intake_agent(ctx)
    q1 = ctx.blocks["p1b1"]["rows"][1]["questions"]
    assert q1[0]["source"] == "agent_verified"                                      # code can see the repeated course
    assert ctx.blocks["p1b2"]["rows"][0]["questions"][0]["source"] == "code_check"
    assert "questions" not in ctx.blocks["p1b1"]["rows"][0]


async def test_if_the_agent_fails_code_reads_every_block_so_the_table_is_never_short(tmp_path):
    ctx = make_ctx(tmp_path, ["not a tool call", "still not", "nothing", read(GOOD), read(SECOND)])
    await run_intake_agent(ctx)
    assert sorted(ctx.by_code) == ["p1b1", "p1b2"] and not ctx.blocks["p1b1"].get("failed") and len(ctx.blocks["p1b2"]["rows"]) == 1


async def test_skipping_is_recorded_and_the_name_check_compares_vision_with_the_record(tmp_path):
    script = [tc("read_block", targets=["p1b1"]), read(GOOD), tc("submit_courses", blocks_read=["p1b1"], skipped=[{"block": "p1b2", "reason": "legend"}], name_seen="Daniel Ferreira", note="x")]
    ctx = make_ctx(tmp_path, script)
    ctx.record_name = "Daniel Ferreira"
    await run_intake_agent(ctx)
    assert ctx.skipped == {"p1b2": "legend"} and "p1b2" not in ctx.blocks and ctx.name_check["status"] == "match"
    ctx2 = make_ctx(tmp_path, [tc("read_block", targets=["p1b1", "p1b2"]), read(GOOD), read(SECOND), tc("run_checks"), tc("submit_courses", blocks_read=["p1b1", "p1b2"], name_seen="Maya Okonkwo")])
    ctx2.record_name = "Daniel Ferreira"
    await run_intake_agent(ctx2)
    assert ctx2.name_check["status"] == "mismatch"


async def test_the_text_layer_is_a_cross_check_only_it_flags_a_disagreement_and_never_changes_what_vision_read(tmp_path, monkeypatch):
    from backend.app.services import cross_check as cc
    monkeypatch.setattr(cc, "block_of", lambda path, pg, title, spans, dpi: "p1b1")
    text = {1: [{"title": "AP Chemistry", "grade": "A", "credits": 1.0, "term": ""}, {"title": "AP Biology", "grade": "B+", "credits": 1.0, "term": ""}, {"title": "AP Art History", "grade": "A", "credits": 1.0, "term": ""}]}
    script = [tc("read_block", targets=["p1b1", "p1b2"]), read(GOOD), read(SECOND), tc("run_checks"), FINISH]
    ctx = make_ctx(tmp_path, script, text=text)
    await run_intake_agent(ctx)
    found = {(f["kind"], f["subject"]) for f in cc.compare(ctx.ordered(), ctx.spans, text, ctx.path, 200)}
    assert found == {("grade_differs", "AP Chemistry"), ("missing_in_vision", "AP Art History")}
    assert ctx.blocks["p1b1"]["rows"][0]["letter"] == "B"                           # vision's value stands; the text layer never overrides it
    assert ctx.blocks["p1b1"]["rows"][0]["questions"][0]["field"] == "grade"


async def test_in_the_pipeline_an_agent_that_cannot_finish_leaves_the_reading_to_code_and_the_application_still_reaches_the_gate(env, monkeypatch):
    from sqlalchemy import select

    from backend.app.config import get_settings
    from backend.app.db.models import Application, TraceEvent
    from backend.app.db.session import app_session
    from backend.tests.conftest import read_to_gate
    monkeypatch.setattr(get_settings(), "intake_agent", True)
    monkeypatch.setattr(get_settings(), "intake_max_tool_rounds", 2)
    up = await read_to_gate(env)
    with app_session() as db:
        app = db.get(Application, up["id"])
        info = app.extraction["intake"]
        assert app.status == "EXTRACTION_REVIEW_REQUIRED" and len(app.rows) == 7 and info["agent"] and info["read_by_code"]
        names = [e.name for e in db.scalars(select(TraceEvent).where(TraceEvent.app_id == up["id"]))]
        assert "intake_summary" in names
    assert "intake" in {s for s, _ in env.model.calls}


async def test_an_empty_reading_of_a_block_with_only_a_few_lines_is_a_footer_not_a_failure(tmp_path):
    ctx = make_ctx(tmp_path, [tc("read_block", targets=["p1b1", "p1b2"]), read(GOOD), read([]), FINISH])
    ctx.lines["p1b2"] = 3                                    # three lines of print: a footer or a legend
    await run_intake_agent(ctx)
    assert not ctx.blocks["p1b2"].get("failed") and ctx.blocks["p1b2"]["rows"] == []


async def test_a_total_the_model_reports_is_dropped_when_the_pages_text_layer_prints_none(tmp_path):
    rows = [{"subject": "AP Biology", "letter": "A-", "credits": 1.0}]
    ctx = make_ctx(tmp_path, [tc("read_block", targets=["p1b1", "p1b2"]), read(rows, term_credits=9.0), read(SECOND), tc("run_checks"), FINISH], text={1: []})
    ctx.text_has_totals = False
    await run_intake_agent(ctx)
    assert ctx.blocks["p1b1"]["totals"]["term_credits"] is None and ctx.blocks["p1b1"]["dropped_totals"] and not ctx.blocks["p1b1"]["flags"]
