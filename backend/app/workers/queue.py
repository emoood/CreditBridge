"""One in-process worker with a queue, running inside the FastAPI app. One job at a time. State lives in the database, so a
restart loses nothing: `recover()` marks jobs that were RUNNING as INTERRUPTED and the resume action continues from the last
DONE unit. Celery and Redis are deliberately not used."""
import asyncio
import contextlib
import logging

from sqlalchemy import select

from .. import runtime
from ..db.models import Application, Job, JobUnit
from ..db.session import app_session
from ..services import prepared, usage
from . import store
from .pipeline import run_audit, run_evaluation, run_reading, run_row


class JobWorker:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.task: asyncio.Task | None = None
        self.current: str | None = None

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task

    def enqueue(self, job_id: str) -> None:
        self.queue.put_nowait(job_id)

    async def idle(self) -> None:
        """Wait until everything queued so far has been processed (used by tests and the live pass)."""
        await self.queue.join()

    async def _loop(self) -> None:
        while True:
            job_id = await self.queue.get()
            self.current = job_id
            try:
                await self.process(job_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logging.getLogger(__name__).exception("job %s crashed", job_id)
                with app_session() as db:
                    job = db.get(Job, job_id)
                    if job and job.status != "DONE":
                        job.status, job.finished_at = "FAILED", store.now()
                        job.flags = [*job.flags, f"crashed:{type(e).__name__}"]
                        db.commit()
                        store.audit(db, job.app_id, "system", "job_failed", job_id, error=type(e).__name__)
            finally:
                self.current = None
                self.queue.task_done()

    async def process(self, job_id: str) -> None:
        with app_session() as db:
            job = db.get(Job, job_id)
            if job is None or job.status not in ("QUEUED", "INTERRUPTED"):
                return
            phase = job.phase
        with app_session() as db:
            job = db.get(Job, job_id)
            demo = ((db.get(Application, job.app_id).extraction or {}).get("demo")) if job else None
        token = None
        if demo:  # a prepared run answers the model's calls; the agents and every tool still run live in code, and nothing here needs Ollama or the network
            provider = prepared.DemoProvider(prepared.load(demo["run"]), prepared.delay_for(demo.get("speed", "fast")))
            token = usage.RECORDED.set(demo["label"])
        else:
            provider = runtime.get_provider()
        try:
            await self._run_phase(phase, job_id, provider)
        finally:
            if token is not None:
                usage.RECORDED.reset(token)

    async def _run_phase(self, phase: str, job_id: str, provider) -> None:
        if phase == "READING":
            await run_reading(job_id, provider)
        elif phase == "AUDIT":
            await run_audit(job_id, provider)
        elif phase == "CASE":
            await run_row(job_id, provider)
        else:
            await run_evaluation(job_id, provider)


def recover(worker: JobWorker) -> dict[str, int]:
    """On startup: RUNNING jobs become INTERRUPTED (their running units become FAILED_RETRYABLE so resume redoes them);
    jobs that were only QUEUED are queued again."""
    interrupted = requeued = 0
    with app_session() as db:
        for job in db.scalars(select(Job).where(Job.status.in_(["RUNNING", "QUEUED"]))):
            if job.status == "RUNNING":
                job.status, job.flags = "INTERRUPTED", [*job.flags, "INTERRUPTED_BY_RESTART"]
                for u in db.scalars(select(JobUnit).where(JobUnit.job_id == job.id, JobUnit.status == "RUNNING")):
                    u.status, u.error_code = "FAILED_RETRYABLE", "interrupted"
                app = db.get(Application, job.app_id)
                db.commit()
                store.audit(db, job.app_id, "system", "job_interrupted", job.id, phase=job.phase, app_status=app.status if app else "")
                interrupted += 1
            else:
                worker.enqueue(job.id)
                requeued += 1
    return {"interrupted": interrupted, "requeued": requeued}


worker = JobWorker()
