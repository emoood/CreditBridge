import asyncio
import time

import pytest
from sqlalchemy import select

from backend.app.db.models import Application, Decision, Job, JobUnit
from backend.app.db.session import app_session
from backend.app.providers.base import LLMRequest
from backend.app.providers.guard import GuardedProvider
from backend.app.workers.queue import JobWorker, recover
from backend.tests.conftest import ADMIN, ScriptedModel, read_to_gate, upload

pytestmark = pytest.mark.asyncio


def units(app_id, stage):
    with app_session() as db:
        return list(db.scalars(select(JobUnit).where(JobUnit.app_id == app_id, JobUnit.stage == stage).order_by(JobUnit.seq)))


async def confirm(env, app_id):
    r = await env.client.post(f"/api/v1/applications/{app_id}/confirm", headers=ADMIN)
    assert r.status_code == 200, r.text
    return r.json()


async def test_upload_returns_job_id_immediately_without_reading(env):
    t0 = time.time()
    up = await upload(env)  # the worker is NOT started, so nothing has been read yet
    assert time.time() - t0 < 2 and up["job_id"] and up["status"] == "READING"
    assert env.model.calls == []
    with app_session() as db:
        assert db.get(Job, up["job_id"]).status == "QUEUED"
        assert db.get(Application, up["id"]).rows == []


async def test_phase1_stops_at_gate_and_phase2_needs_confirmation(env):
    up = await read_to_gate(env)
    with app_session() as db:
        app = db.get(Application, up["id"])
        assert app.status == "EXTRACTION_REVIEW_REQUIRED" and len(app.rows) == 7
        assert db.scalars(select(Job).where(Job.phase == "EVALUATION")).first() is None  # Phase 2 did not start by itself
    assert [s for s, _ in env.model.calls if s != "read"] == []  # no agent call happened
    r = await env.client.post(f"/api/v1/applications/{up['id']}/evaluate", headers=ADMIN)
    assert r.status_code == 409  # explicit evaluate is refused before confirmation


async def test_confirm_records_officer_time_and_corrections_then_queues_phase2(env):
    up = await read_to_gate(env)
    row = (await env.client.get(f"/api/v1/applications/{up['id']}")).json()["rows"][1]
    await env.client.patch(f"/api/v1/applications/{up['id']}/rows/{row['id']}", json={"grade": "A"}, headers=ADMIN)
    out = await confirm(env, up["id"])
    with app_session() as db:
        app = db.get(Application, up["id"])
        assert app.confirmed_by == "Test Officer" and app.confirmed_at is not None and app.status == "CONFIRMED"
        assert any(c.get("field") == "grade" and c["to"] == "A" for c in app.corrections)
        assert db.get(Job, out["job_id"]).phase == "EVALUATION"
    r = await env.client.post("/api/v1/applications/" + up["id"] + "/confirm", headers={"X-Role": "registrar"})
    assert r.status_code == 403


async def test_replaying_confirm_does_not_queue_phase2_twice(env):
    up = await read_to_gate(env)
    queued: list[str] = []
    real = env.worker.enqueue
    env.worker.enqueue = lambda jid: (queued.append(jid), real(jid))[1]  # record every enqueue
    first, second, third = await confirm(env, up["id"]), await confirm(env, up["id"]), await confirm(env, up["id"])
    assert not first["already_confirmed"] and second["already_confirmed"] and third["already_confirmed"]
    assert second["job_id"] == first["job_id"] == third["job_id"]
    assert queued == [first["job_id"]]  # enqueued exactly once
    with app_session() as db:
        assert len(list(db.scalars(select(Job).where(Job.phase == "EVALUATION")))) == 1


async def test_hung_call_is_cut_off_and_the_job_continues(env):
    up = await read_to_gate(env)
    sids = None
    out = await confirm(env, up["id"])
    with app_session() as db:
        sids = [r["source_id"] for r in db.get(Application, up["id"]).rows]
    env.model.hang_for = {sids[1]}
    t0 = time.time()
    await env.worker.idle()
    assert time.time() - t0 < 5  # 0.3 s limit x2 attempts, not an hour
    us = {u.unit_key: u for u in units(up["id"], "evaluate")}
    assert us[sids[1]].status == "FAILED_RETRYABLE" and us[sids[1]].error_code == "timeout"
    assert all(us[s].status == "DONE" for s in sids if s != sids[1])
    with app_session() as db:
        assert db.get(Job, out["job_id"]).status == "DONE" and db.get(Application, up["id"]).status == "EVALUATION_PARTIAL"


async def test_one_failed_subject_does_not_block_the_others(env):
    up = await read_to_gate(env)
    await confirm(env, up["id"])
    with app_session() as db:
        sids = [r["source_id"] for r in db.get(Application, up["id"]).rows]
    env.model.fail_for = {sids[0], sids[4]}  # sids[3] is the unrecognized subject, which never calls the model
    await env.worker.idle()
    st = {u.unit_key: (u.status, u.error_code) for u in units(up["id"], "evaluate")}
    assert st[sids[0]] == ("FAILED_RETRYABLE", "model_error") and st[sids[4]][0] == "FAILED_RETRYABLE"
    assert sum(1 for v in st.values() if v[0] == "DONE") == 5
    res = (await env.client.get(f"/api/v1/applications/{up['id']}/results")).json()
    assert len(res["results"]) == 5 and len(res["pending_units"]) == 2


async def test_unrecognized_subject_gets_title_only_suggestions_never_a_match(env):
    up = await read_to_gate(env)
    rows = (await env.client.get(f"/api/v1/applications/{up['id']}")).json()["rows"]
    unrec = [r for r in rows if not r["subject"]]
    assert [r["title"] for r in unrec] == ["Film Appreciation"] and unrec[0]["curriculum_status"] == "NEEDS_SYLLABUS"
    # a hostile model: claims a full match, cites things, and proposes 5 courses (one invented, one twice)
    hostile = {"recommendation": "POTENTIAL_MATCH", "target_course_id": "CS101", "evidence_strength": "STRONG",
                                 "supported_outcomes": [{"source_excerpt_id": "x", "target_excerpt_id": "tgt-CS101-o1", "explanation": "same"}],
                                 "title_only_suggestions": [{"target_course_id": c, "reason": "title"} for c in ("CS101", "ZZZ999", "CS101", "CS330", "COMP220")]}
    idx = rows.index(unrec[0]) + 1  # source ids are assigned at confirm: <short app id>-<position>
    env.model.agent_args[f"{up['id'].replace('-', '')[-6:].upper()}-{idx:02d}"] = hostile
    await confirm(env, up["id"])
    await env.worker.idle()
    res = (await env.client.get(f"/api/v1/applications/{up['id']}/results")).json()
    by = {r["course"]["title"]: r for r in res["results"]}
    r = by["Film Appreciation"]
    assert r["match"]["status"] == "NEEDS_SYLLABUS" and r["triage"]["category"] == "NEEDS_EVIDENCE"  # never a match, never ready
    assert r["match"]["target"] is None and r["match"]["supported_outcomes"] == [] and r["review"] is None
    sug = r["match"]["title_only_suggestions"]
    assert 1 <= len(sug) <= 3 and all(s["basis"] == "TITLE_ONLY_UNVERIFIED" and s["target"] for s in sug)
    assert len({s["target"]["code"] for s in sug}) == len(sug) and "ZZZ999" not in {s["target"]["code"] for s in sug}
    assert r["syllabus_requests"] and "TITLE_ONLY_UNVERIFIED" in r["match"]["flags"]
    assert len(res["results"]) == 7 and res["job"]["status"] == "DONE"
    assert not any(stage == "review" and unit == r["course"]["code"] for stage, unit in env.model.calls)  # never reviewed


async def test_restart_marks_interrupted_and_resume_skips_done_units(env):
    up = await read_to_gate(env)
    out = await confirm(env, up["id"])
    await env.worker.idle()
    with app_session() as db:  # simulate a crash mid-run: last unit was RUNNING, the job RUNNING
        us = list(db.scalars(select(JobUnit).where(JobUnit.job_id == out["job_id"], JobUnit.stage == "evaluate").order_by(JobUnit.seq)))
        last = us[-1]
        keep = {u.unit_key: u.finished_at for u in us[:-1]}
        last.status, last.result = "RUNNING", None
        db.get(Job, out["job_id"]).status = "RUNNING"
        db.commit()
        last_key = last.unit_key
    assert recover(env.worker)["interrupted"] == 1
    with app_session() as db:
        assert db.get(Job, out["job_id"]).status == "INTERRUPTED"
        assert db.scalar(select(JobUnit).where(JobUnit.unit_key == last_key, JobUnit.stage == "evaluate")).status == "FAILED_RETRYABLE"
    env.model.calls.clear()
    r = await env.client.post(f"/api/v1/jobs/{out['job_id']}/resume", headers=ADMIN)
    assert r.status_code == 200
    await env.worker.idle()
    assert {u for _, u in env.model.calls if u} == {last_key}  # only the unfinished unit was redone
    with app_session() as db:
        done = {u.unit_key: u for u in db.scalars(select(JobUnit).where(JobUnit.job_id == out["job_id"], JobUnit.stage == "evaluate"))}
        assert all(u.status == "DONE" for u in done.values()) and all(done[k].finished_at == v for k, v in keep.items())
        assert db.get(Application, up["id"]).status == "REVIEW_READY"


async def test_rerun_never_creates_or_changes_a_human_decision(env):
    up = await read_to_gate(env)
    await confirm(env, up["id"])
    await env.worker.idle()
    res = (await env.client.get(f"/api/v1/applications/{up['id']}/results")).json()["results"]
    sid = res[0]["course"]["code"]
    r = await env.client.post(f"/api/v1/applications/{up['id']}/results/{sid}/decision", headers=ADMIN,
                              json={"decision": "DECLINED", "reason": "not equivalent", "expected_version": 1, "idempotency_key": "k1"})
    assert r.status_code == 200
    with app_session() as db:
        before = [(d.id, d.decision, d.reason) for d in db.scalars(select(Decision))]
    assert (await env.client.post(f"/api/v1/applications/{up['id']}/evaluate", headers=ADMIN)).status_code == 200
    await env.worker.idle()
    with app_session() as db:
        assert [(d.id, d.decision, d.reason) for d in db.scalars(select(Decision))] == before  # untouched, none added
    after = (await env.client.get(f"/api/v1/applications/{up['id']}/results")).json()["results"]
    mine = next(x for x in after if x["course"]["code"] == sid)
    assert mine["decision"]["decision"] == "DECLINED" and mine["decision"]["stale"] is True  # answered an earlier suggestion


async def test_two_queued_jobs_never_call_the_model_at_once(env):
    env.model.delay = 0.05
    await upload(env)
    await upload(env)  # two reading jobs queued
    env.worker.start()
    await env.worker.idle()
    assert env.model.max_active == 1
    g = GuardedProvider(ScriptedModel(), timeout=2)
    g.inner.delay = 0.05
    await asyncio.gather(*(g.complete(LLMRequest([], stage="x")) for _ in range(5)))  # five concurrent callers, e.g. two jobs' agents
    assert g.inner.max_active == 1


async def test_failed_block_is_reread_once_and_flags_surface(env):
    bad = [{"subject": "AP Statistics", "mark": 85, "letter": "B+", "points": 3.0, "credits": 1.0}]  # B+ does not match mark 85 -> B
    good = [{"subject": "AP Statistics", "mark": 85, "letter": "B", "points": 3.0, "credits": 1.0}]
    env.model.read_script = [bad, good]
    up = await read_to_gate(env)
    us = units(up["id"], "read")
    assert us[0].status == "DONE" and us[0].method == "vision_reread" and us[0].attempts == 2
    assert us[0].result["rows"][0]["letter"] == "B" and not us[0].result["flags"]  # the re-read fixed it
    env.model.read_script = [bad, bad]
    env.model.calls.clear()  # the scripted model indexes its canned reads by call count
    up2 = await read_to_gate(env)
    flagged = units(up2["id"], "read")[0]
    assert flagged.status == "DONE" and any("letter_mismatch" in f for f in flagged.result["flags"])  # still wrong: surfaced to the officer


async def test_a_block_that_keeps_failing_is_marked_and_does_not_stop_reading(env):
    up = await upload(env)
    with app_session() as db:
        job_id = up["job_id"]
    env.model.fail_for = {"p1b1"}
    env.worker.start()
    await env.worker.idle()
    us = units(up["id"], "read")
    assert us[0].status == "FAILED_RETRYABLE" and us[0].error_code == "model_error"
    with app_session() as db:
        assert db.get(Job, job_id).status == "DONE" and "BLOCKS_FAILED" in db.get(Job, job_id).flags
        assert db.get(Application, up["id"]).status == "EXTRACTION_REVIEW_REQUIRED"


async def test_worker_survives_a_crashing_job(env):
    w = JobWorker()
    w.start()
    w.enqueue("does-not-exist")
    await w.idle()
    assert w.task and not w.task.done()
    await w.stop()


async def test_record_then_replay_labels_every_replayed_result_and_runs_chosen_subjects_live(env, monkeypatch, tmp_path):
    from backend.app.config import get_settings
    monkeypatch.setattr(get_settings(), "recordings_root", str(tmp_path))
    up = await read_to_gate(env)
    await confirm(env, up["id"])
    await env.worker.idle()
    first = (await env.client.get(f"/api/v1/applications/{up['id']}/results")).json()
    assert len(first["results"]) == 7 and not any(r["is_replay"] for r in first["results"])
    assert (await env.client.post("/api/v1/recordings", json={"app_id": up["id"], "name": "t1"}, headers={"X-Role": "registrar"})).status_code == 403
    saved = (await env.client.post("/api/v1/recordings", json={"app_id": up["id"], "name": "t1"}, headers=ADMIN)).json()
    assert saved["name"] == "t1" and len(saved["subjects"]) == 7 and saved["label"].startswith("Replay of recorded run, ") and saved["label"].endswith(", fake")
    assert [r["name"] for r in (await env.client.get("/api/v1/recordings")).json()] == ["t1"]

    title = first["results"][0]["course"]["title"]
    calls_before = len(env.model.calls)
    rp = (await env.client.post("/api/v1/recordings/t1/replay", json={"live": [title]}, headers=ADMIN)).json()
    assert rp["status"] == "EXTRACTION_REVIEW_REQUIRED" and rp["replay"]["label"] == saved["label"] and rp["courses"] == 7
    assert (await env.client.post(f"/api/v1/applications/{rp['id']}/evaluate", headers=ADMIN)).status_code == 409  # the gate still applies
    assert len(env.model.calls) == calls_before  # starting a replay never calls the model
    await confirm(env, rp["id"])
    await env.worker.idle()
    res = (await env.client.get(f"/api/v1/applications/{rp['id']}/results")).json()
    assert len(res["results"]) == 7 and res["job"]["status"] == "DONE"
    live = [r for r in res["results"] if not r["is_replay"]]
    replayed = [r for r in res["results"] if r["is_replay"]]
    assert [r["course"]["title"] for r in live] == [title] and len(replayed) == 6
    assert all(r["replay"]["label"] == saved["label"] for r in replayed) and all("replay" not in r for r in live)
    new_calls = env.model.calls[calls_before:]
    assert new_calls and {u for _, u in new_calls} == {live[0]["course"]["code"]}  # the model ran for the live subject only
    by_title = {r["course"]["title"]: r for r in first["results"]}
    assert all(r["triage"] == by_title[r["course"]["title"]]["triage"] and r["match"] == by_title[r["course"]["title"]]["match"] for r in replayed)


async def test_replay_with_nothing_live_makes_no_model_call_and_a_changed_row_falls_back_to_live(env, monkeypatch, tmp_path):
    from backend.app.config import get_settings
    monkeypatch.setattr(get_settings(), "recordings_root", str(tmp_path))
    up = await read_to_gate(env)
    await confirm(env, up["id"])
    await env.worker.idle()
    await env.client.post("/api/v1/recordings", json={"app_id": up["id"], "name": "t2"}, headers=ADMIN)
    n = len(env.model.calls)
    rp = (await env.client.post("/api/v1/recordings/t2/replay", json={}, headers=ADMIN)).json()
    row = (await env.client.get(f"/api/v1/applications/{rp['id']}")).json()["rows"][0]
    assert (await env.client.patch(f"/api/v1/applications/{rp['id']}/rows/{row['id']}", json={"title": "Something Else"}, headers=ADMIN)).status_code == 200
    await confirm(env, rp["id"])
    await env.worker.idle()
    res = (await env.client.get(f"/api/v1/applications/{rp['id']}/results")).json()
    assert sum(r["is_replay"] for r in res["results"]) == 6 and len({u for _, u in env.model.calls[n:]}) == 1  # only the edited row ran live
    bad = await env.client.post("/api/v1/recordings/t2/replay", json={"live": ["No Such Subject"]}, headers=ADMIN)
    assert bad.status_code == 422
    assert (await env.client.post("/api/v1/recordings/nope/replay", json={}, headers=ADMIN)).status_code == 404


async def test_school_specific_subjects_are_flagged_for_the_officer_and_never_reach_the_model(env, monkeypatch, tmp_path):
    from backend.app.agents import orchestrator
    f = tmp_path / "s.yaml"
    f.write_text("patterns:\n  - film appreciation\n")
    monkeypatch.setattr(orchestrator, "SCHOOL_SPECIFIC_FILE", f)
    up = await read_to_gate(env)
    rows = (await env.client.get(f"/api/v1/applications/{up['id']}")).json()["rows"]
    idx = [r["title"] for r in rows].index("Film Appreciation") + 1
    await confirm(env, up["id"])
    await env.worker.idle()
    res = (await env.client.get(f"/api/v1/applications/{up['id']}/results")).json()
    r = next(x for x in res["results"] if x["course"]["title"] == "Film Appreciation")
    assert r["match"]["status"] == "SCHOOL_SPECIFIC" and r["triage"]["category"] == "SCHOOL_SPECIFIC"
    assert "SCHOOL_SPECIFIC_NOT_MAPPED" in r["match"]["flags"] and r["match"]["target"] is None and r["match"]["title_only_suggestions"] == []
    assert r["llm_calls"] == 0 and not any(u == r["course"]["code"] for _, u in env.model.calls) and idx
    assert orchestrator.is_school_specific("Moral Education") is False  # patched list only; the shipped list is tested below


def test_shipped_list_covers_the_named_school_specific_subjects():
    from backend.app.agents.orchestrator import is_school_specific
    for t in ("Moral Education", "Physical Education", "Islamic A", "Arabic A", "UAE Social Studies"):
        assert is_school_specific(t), t
    for t in ("AP Biology", "AP Calculus AB", "Computer Science Principles", "English 12"):
        assert not is_school_specific(t), t
