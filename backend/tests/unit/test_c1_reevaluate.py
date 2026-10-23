"""C1 step 3, per-field Re-evaluate: a cell, a row or a page is read again at a higher zoom WITHOUT the old value, the result is a proposal the officer picks
from, code recomputes what depends on it, and a field the officer edited is never replaced by AI. No live model: the scripted test provider answers the reads."""
import base64
import copy
import io
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from backend.app.agents import intake_agent as ia
from backend.app.db.models import Application, AuditEvent, UsageEntry
from backend.app.db.session import app_session
from backend.app.services import grade_checks as gc
from backend.tests.conftest import ADMIN, FALL, SPRING, read_to_gate

ENGLISH = 1  # position of "AP English Language and Composition" in the first block


def reread_of(rows):
    return copy.deepcopy(rows)


async def at_gate(env):
    """Reads the fixture with the scripted model and spies on every request the model gets."""
    seen = []
    orig = env.model.complete

    async def spy(request):
        seen.append(request)
        return await orig(request)
    env.model.complete = spy
    up = await read_to_gate(env)
    env.seen = seen
    d = (await env.client.get(f"/api/v1/applications/{up['id']}")).json()
    return up["id"], d


def script(env, *rereads):
    """The scripted model answers reads by count: the first reading used some calls (a block retried), so the re-reads follow them."""
    used = sum(1 for stage, _u in env.model.calls if stage == "read")
    env.model.read_script = [FALL, SPRING, *([[]] * (used - 2)), *rereads]


def row_named(d, title):
    return next(r for r in d["rows"] if r["title"] == title)


async def reevaluate(env, app_id, **body):
    return await env.client.post(f"/api/v1/applications/{app_id}/reevaluate", json=body, headers=ADMIN)


@pytest.mark.asyncio
async def test_a_cell_is_read_again_at_a_higher_zoom_without_the_old_value_and_only_proposed(env):
    app_id, d = await at_gate(env)
    english = row_named(d, "AP English Language and Composition")
    reads_before = sum(1 for r in env.seen if r.stage == "read")
    fresh = reread_of(FALL)
    fresh[ENGLISH] = {**fresh[ENGLISH], "mark": 91, "letter": "A-", "points": 3.7}
    script(env, fresh)
    r = await reevaluate(env, app_id, scope="field", row_id=english["id"], field="grade")
    assert r.status_code == 200, r.text
    (p,) = r.json()["proposals"]
    assert (p["field"], p["old"], p["new"], p["same"]) == ("grade", "B+", "A-", False)
    assert r.json()["calls_used"] == 1
    # the model got one read call, a larger picture, and not one word of what the table says now
    reads = [q for q in env.seen if q.stage == "read"]
    assert len(reads) == reads_before + 1
    ask = reads[-1].messages[-1]["content"]
    assert "course row number 2 of this block" in ask and "B+" not in ask and "88" not in ask and "English" not in ask
    width = lambda q: Image.open(io.BytesIO(base64.b64decode(q.messages[-1]["images"][0]))).width
    assert width(reads[-1]) > 1.4 * width(reads[0])
    # nothing in the table changed yet: the value is a proposal stored on the row
    after = (await env.client.get(f"/api/v1/applications/{app_id}")).json()
    row = row_named(after, "AP English Language and Composition")
    assert row["grade"] == "B+" and row["reeval"]["grade"]["new"] == "A-" and not row.get("edited")
    # the call is in the ledger and the audit trail (ids and counts, never values)
    with app_session() as db:
        assert db.scalars(select(UsageEntry).where(UsageEntry.app_id == app_id, UsageEntry.model == "reevaluate")).first() is not None
        ev = db.scalars(select(AuditEvent).where(AuditEvent.app_id == app_id, AuditEvent.event == "reevaluate")).one()
        assert "A-" not in str(ev.details) and ev.details["proposals"] == 1


@pytest.mark.asyncio
async def test_picking_the_new_value_updates_the_table_the_checks_and_the_totals_and_marks_it_the_officers(env):
    app_id, d = await at_gate(env)
    english = row_named(d, "AP English Language and Composition")
    assert not any("credits_mismatch" in f for f in d["extraction"]["flags"])
    fresh = reread_of(FALL)
    fresh[ENGLISH] = {**fresh[ENGLISH], "credits": 2.0}
    script(env, fresh, fresh)
    await reevaluate(env, app_id, scope="field", row_id=english["id"], field="credits")
    r = await env.client.post(f"/api/v1/applications/{app_id}/rows/{english['id']}/reeval/credits", json={"choice": "new"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["credits"] == "2" and "reeval" not in row
    assert row["edited"]["credits"]["by"] == "Test Officer" and row["edited"]["credits"]["via"] == "re-read" and row["corrected"]
    after = (await env.client.get(f"/api/v1/applications/{app_id}")).json()
    assert any("term_credits_mismatch(computed 4, printed 3)" in f for f in after["extraction"]["flags"])   # the term total is checked again with the new credits
    with app_session() as db:
        events = [e.event for e in db.scalars(select(AuditEvent).where(AuditEvent.app_id == app_id))]
    assert "reeval_chosen" in events


@pytest.mark.asyncio
async def test_a_grade_that_fails_its_check_is_flagged_after_the_pick_and_choosing_the_old_value_changes_nothing(env):
    app_id, d = await at_gate(env)
    english = row_named(d, "AP English Language and Composition")
    fresh = reread_of(FALL)
    fresh[ENGLISH] = {**fresh[ENGLISH], "mark": 88, "letter": "C", "points": 3.3}   # a letter that does not fit the mark
    script(env, fresh, fresh)
    await reevaluate(env, app_id, scope="field", row_id=english["id"], field="grade")
    keep = await env.client.post(f"/api/v1/applications/{app_id}/rows/{english['id']}/reeval/grade", json={"choice": "old"}, headers=ADMIN)
    assert keep.json()["grade"] == "B+" and "reeval" not in keep.json() and not keep.json().get("edited")
    await reevaluate(env, app_id, scope="field", row_id=english["id"], field="grade")
    row = (await env.client.post(f"/api/v1/applications/{app_id}/rows/{english['id']}/reeval/grade", json={"choice": "new"}, headers=ADMIN)).json()
    assert row["grade"] == "C" and any(f.startswith("letter_mismatch") for f in row["flags"])   # code checked it again and says so
    assert (await env.client.post(f"/api/v1/applications/{app_id}/rows/{english['id']}/reeval/grade", json={"choice": "new"}, headers=ADMIN)).status_code == 404   # nothing left to choose


@pytest.mark.asyncio
async def test_a_re_read_that_agrees_is_reported_as_the_same_and_changes_nothing(env):
    app_id, d = await at_gate(env)
    english = row_named(d, "AP English Language and Composition")
    script(env, FALL, FALL)
    (p,) = (await reevaluate(env, app_id, scope="field", row_id=english["id"], field="grade")).json()["proposals"]
    assert p["same"] is True and p["new"] == p["old"] == "B+"
    row = (await env.client.post(f"/api/v1/applications/{app_id}/rows/{english['id']}/reeval/grade", json={"choice": "new"}, headers=ADMIN)).json()
    assert row["grade"] == "B+" and not row.get("edited")


@pytest.mark.asyncio
async def test_a_field_the_officer_edited_is_never_replaced_and_re_reading_it_asks_first(env):
    app_id, d = await at_gate(env)
    english = row_named(d, "AP English Language and Composition")
    await env.client.patch(f"/api/v1/applications/{app_id}/rows/{english['id']}", json={"grade": "A"}, headers=ADMIN)
    edited = row_named((await env.client.get(f"/api/v1/applications/{app_id}")).json(), "AP English Language and Composition")
    assert edited["edited"]["grade"]["by"] == "Test Officer" and edited["edited"]["grade"]["via"] == "typed" and edited["edited"]["grade"]["at"]
    calls = len(env.model.calls)
    ask = await reevaluate(env, app_id, scope="field", row_id=english["id"], field="grade")
    assert ask.status_code == 409 and "Edited by Test Officer" in ask.json()["detail"]
    assert len(env.model.calls) == calls                                                   # asking first costs no model call
    fresh = reread_of(FALL)
    fresh[ENGLISH] = {**fresh[ENGLISH], "mark": 91, "letter": "A-", "points": 3.7}
    script(env, fresh, fresh)
    row_scope = (await reevaluate(env, app_id, scope="row", row_id=english["id"])).json()   # a row re-read leaves the edited field alone
    assert not any(p["field"] == "grade" for p in row_scope["proposals"])
    assert any(n.get("field") == "grade" and "edited by the officer" in n["why"] for n in row_scope["notes"])
    ok = await reevaluate(env, app_id, scope="field", row_id=english["id"], field="grade", confirm_overwrite=True)
    assert ok.status_code == 200 and ok.json()["proposals"][0]["new"] == "A-"
    now = row_named((await env.client.get(f"/api/v1/applications/{app_id}")).json(), "AP English Language and Composition")
    assert now["grade"] == "A"                                                              # still the officer's value until they choose the new one


@pytest.mark.asyncio
async def test_typing_over_a_proposal_removes_it(env):
    app_id, d = await at_gate(env)
    english = row_named(d, "AP English Language and Composition")
    fresh = reread_of(FALL)
    fresh[ENGLISH] = {**fresh[ENGLISH], "mark": 91, "letter": "A-", "points": 3.7}
    script(env, fresh)
    await reevaluate(env, app_id, scope="field", row_id=english["id"], field="grade")
    row = (await env.client.patch(f"/api/v1/applications/{app_id}/rows/{english['id']}", json={"grade": "B"}, headers=ADMIN)).json()
    assert "grade" not in (row.get("reeval") or {})


@pytest.mark.asyncio
async def test_a_page_re_read_reads_each_block_once_and_proposes_every_difference(env):
    app_id, d = await at_gate(env)
    blocks = [b for b in d["extraction"]["blocks"] if b["status"] == "DONE"]
    first, second = copy.deepcopy(FALL), copy.deepcopy(SPRING)
    first[0] = {**first[0], "mark": 91, "letter": "A-", "points": 3.7}
    second[1] = {**second[1], "subject": "AP Statistics (Honors)"}
    script(env, first, second)
    reads_before = sum(1 for q in env.seen if q.stage == "read")
    r = await reevaluate(env, app_id, scope="page", page=1)
    assert r.status_code == 200, r.text
    assert sum(1 for q in env.seen if q.stage == "read") == reads_before + 2 and r.json()["calls_used"] == 2 and len(blocks) >= 2
    changed = {(p["row"], p["field"]): p for p in r.json()["proposals"] if not p["same"]}
    calc, stats = row_named(d, "AP Calculus AB"), row_named(d, "AP Statistics")
    assert changed[(calc["id"], "grade")]["new"] == "A-" and changed[(stats["id"], "title")]["new"] == "AP Statistics (Honors)"
    assert len(changed) == 2
    ask = [q for q in env.seen if q.stage == "read"][-1].messages[-1]["content"]
    assert "course row number" not in ask                                                   # a page re-read has no single row to look at


@pytest.mark.asyncio
async def test_a_new_title_clears_an_automatic_link_and_links_again(env):
    app_id, d = await at_gate(env)
    calc = row_named(d, "AP Calculus AB")
    assert calc["subject"]["name"] == "Calculus AB"
    fresh = reread_of(FALL)
    fresh[0] = {**fresh[0], "subject": "AP Statistics"}
    script(env, fresh)
    await reevaluate(env, app_id, scope="field", row_id=calc["id"], field="title")
    row = (await env.client.post(f"/api/v1/applications/{app_id}/rows/{calc['id']}/reeval/title", json={"choice": "new"}, headers=ADMIN)).json()
    assert row["title"] == "AP Statistics" and row["subject"]["name"] == "Statistics" and row["edited"]["title"]["via"] == "re-read"


@pytest.mark.asyncio
async def test_what_cannot_be_re_read_is_refused_before_any_model_call(env):
    app_id, d = await at_gate(env)
    calls = len(env.model.calls)
    added = (await env.client.post(f"/api/v1/applications/{app_id}/rows", headers=ADMIN)).json()
    r = await reevaluate(env, app_id, scope="field", row_id=added["id"], field="grade")
    assert r.status_code == 409 and "added by hand" in r.json()["detail"]
    assert (await reevaluate(env, app_id, scope="field", row_id="nope", field="grade")).status_code == 404
    english = row_named(d, "AP English Language and Composition")
    assert (await reevaluate(env, app_id, scope="field", row_id=english["id"], field="mark")).status_code == 422
    with app_session() as db:                                                                # the case has used its re-evaluation budget
        a = db.get(Application, app_id)
        a.extraction = {**a.extraction, "reeval": {"calls": ia.REEVAL_CALL_BUDGET}}
        db.commit()
    over = await reevaluate(env, app_id, scope="field", row_id=english["id"], field="grade")
    assert over.status_code == 409 and "budget" in over.json()["detail"]
    with app_session() as db:                                                                # a case that replays a recording has no live model to ask
        a = db.get(Application, app_id)
        a.extraction = {**a.extraction, "reeval": {}, "demo": {"run": "x", "label": "x", "speed": "fast"}}
        db.commit()
    replay = await reevaluate(env, app_id, scope="field", row_id=english["id"], field="grade")
    assert replay.status_code == 409 and "Live AI" in replay.json()["detail"]
    assert len(env.model.calls) == calls


@pytest.mark.asyncio
async def test_a_failed_re_read_changes_nothing_and_says_so(env):
    app_id, d = await at_gate(env)
    english = row_named(d, "AP English Language and Composition")
    env.model.fail_for = {"p1b1"}
    r = await reevaluate(env, app_id, scope="field", row_id=english["id"], field="grade")
    assert r.status_code == 200 and r.json()["proposals"] == [] and "failed" in r.json()["notes"][0]["why"]
    assert row_named((await env.client.get(f"/api/v1/applications/{app_id}")).json(), "AP English Language and Composition")["grade"] == "B+"


# ---------------------------------------------------------------------------------------------------------------------- the engine, without the API
def test_rows_are_located_by_position_and_a_count_that_disagrees_locates_nothing():
    units = [("p1b1", {"rows": [{"subject": "A", "mark": 90}, {"subject": "heading"}, {"subject": "B", "letter": "B"}]}), ("p1b2", {"rows": [{"subject": "C", "credits": 1.0}]})]
    assert ia.locate_rows(units, ["r1", "r2", "r3"]) == {"r1": ("p1b1", 0), "r2": ("p1b1", 1), "r3": ("p1b2", 0)}   # the heading is not a course row
    assert ia.locate_rows(units, ["r1", "r2"]) is None and ia.locate_rows(units, ["r1", "r2", "r3", "r4"]) is None


def test_a_row_is_matched_by_position_or_else_by_a_title_that_is_close():
    old = {"rows": [{"subject": "AP Calculus AB", "mark": 90}, {"subject": "AP Biology", "mark": 80}]}
    same_count = {"rows": [{"subject": "AP Calculus AB", "mark": 91}, {"subject": "AP Biologyy", "mark": 81}]}
    assert ia.match_row(same_count, old, 1) == (same_count["rows"][1], "position")
    extra = {"rows": [{"subject": "Physics", "mark": 70}, *same_count["rows"]]}
    assert ia.match_row(extra, old, 1) == (extra["rows"][2], "title")                       # the counts differ: the closest title is the same row
    assert ia.match_row({"rows": [{"subject": "Art", "mark": 70}, {"subject": "Music", "mark": 70}, {"subject": "Dance", "mark": 70}]}, old, 0) == (None, "")


def test_values_are_written_the_way_the_table_writes_them():
    assert ia.row_values({"subject": "X", "code": None, "letter": None, "mark": 91.0, "credits": 0.5, "term": None}) == {"code": "", "title": "X", "credits": "0.5", "grade": "91", "term": ""}
    assert ia.same_value("credits", "1", "1.0") and ia.same_value("grade", "b+", "B+") and not ia.same_value("grade", "B", "B+")


@pytest.mark.asyncio
async def test_recomputing_without_a_chosen_value_gives_the_flags_the_reading_made(env):
    app_id, d = await at_gate(env)
    with app_session() as db:
        from backend.app.api.intake import _reading_units
        units = _reading_units(db, db.get(Application, app_id))
    merged = ia.recompute_extraction(units, [], gc.load_scale())
    assert merged["flags"] == d["extraction"]["flags"] and merged["cumulative"] == d["extraction"]["cumulative"]


def test_an_image_file_is_enlarged_for_a_higher_dpi_so_the_crops_land_on_the_same_rows():
    from backend.app.services import vision_reader as vr
    path = str(Path(__file__).resolve().parents[3] / "fixtures" / "demo_v2" / "school_transcript.png")
    base, high = vr.render_pages(path, 200)[0], vr.render_pages(path, 300)[0]
    assert high.width == round(base.width * 1.5) and high.height == round(base.height * 1.5)
