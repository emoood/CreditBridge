"""Cancel a running or queued job: the worker keeps running, the job is INTERRUPTED with a flag, and Resume works."""
import asyncio

from backend.app.db.models import Job
from backend.app.db.session import app_session
from backend.tests.conftest import ADMIN, upload


async def _wait_for(pred, tries=100):
    for _ in range(tries):
        if pred():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_cancel_a_running_job_keeps_the_worker_alive_and_can_be_resumed(env, monkeypatch):
    from backend.app.workers import queue as q
    started = asyncio.Event()

    async def slow_reading(job_id, provider):
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(q, "run_reading", slow_reading)
    up = await upload(env)
    env.worker.start()
    await asyncio.wait_for(started.wait(), 5)
    with app_session() as db:
        db.get(Job, up["job_id"]).status = "RUNNING"
        db.commit()
    r = await env.client.post(f"/api/v1/jobs/{up['job_id']}/cancel", headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"] == "CANCELLING"
    assert await _wait_for(lambda: env.worker.current is None)
    with app_session() as db:
        job = db.get(Job, up["job_id"])
        assert job.status == "INTERRUPTED" and "CANCELLED_BY_OFFICER" in job.flags
    assert env.worker.task is not None and not env.worker.task.done()   # the worker carries on
    # cancelling again is refused, and resume clears the flag and queues the job
    assert (await env.client.post(f"/api/v1/jobs/{up['job_id']}/cancel", headers=ADMIN)).status_code == 409
    monkeypatch.setattr(q, "run_reading", lambda job_id, provider: asyncio.sleep(0))
    assert (await env.client.post(f"/api/v1/jobs/{up['job_id']}/resume", headers=ADMIN)).status_code == 200
    with app_session() as db:
        assert "CANCELLED_BY_OFFICER" not in db.get(Job, up["job_id"]).flags


async def test_only_an_admissions_officer_can_cancel(env):
    up = await upload(env)
    r = await env.client.post(f"/api/v1/jobs/{up['job_id']}/cancel", headers={"X-Role": "registrar"})
    assert r.status_code == 403


async def test_a_queued_job_that_was_cancelled_is_skipped(env, monkeypatch):
    from backend.app.workers import queue as q
    ran = []

    async def reading(job_id, provider):
        ran.append(job_id)

    monkeypatch.setattr(q, "run_reading", reading)
    up = await upload(env)   # queued, the worker has not started
    r = await env.client.post(f"/api/v1/jobs/{up['job_id']}/cancel", headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"] == "INTERRUPTED"
    env.worker.start()
    await env.worker.idle()
    assert ran == []
