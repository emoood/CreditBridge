import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .api.cases import router as cases_router
from .api.common import seed_demo
from .api.evaluation import router as evaluation_router
from .api.intake import router as intake_router
from .api.officer import router as officer_router
from .api.recordings import router as recordings_router
from .config import get_settings
from .db.session import app_session, init_app_db
from .devlog import events
from .providers.router import build_provider
from .providers.warmup import warm_up
from .services.mapping import seed_mappings
from .workers.queue import recover, worker


async def _warm() -> None:
    r = await warm_up(get_settings())
    events.emit("stage", stage="warmup", **{k: v for k, v in r.items() if k in ("ok", "model", "load_s", "warm_call_s", "thinking_chars", "error")})


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_app_db()
    with app_session() as db:
        seed_demo(db)
        seed_mappings(db)
    recover(worker)  # RUNNING jobs -> INTERRUPTED; QUEUED jobs are queued again
    worker.start()
    warm = asyncio.create_task(_warm()) if get_settings().warmup_on_start else None
    yield
    if warm:
        warm.cancel()
    await worker.stop()


app = FastAPI(title="CreditBridge API (synthetic demo data)", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


app.include_router(intake_router)
app.include_router(evaluation_router)
app.include_router(cases_router)
app.include_router(officer_router)
app.include_router(recordings_router)


@app.get("/api/v1/health")
async def health():
    s = get_settings()
    p = await build_provider(s).health()
    return {"app": "ok", "demo_mode": s.demo_mode, "database": "not configured" if not s.database_url else "configured",
            "provider": {"ok": p.ok, "name": p.provider, "model": p.model, "detail": p.detail}}


@app.get("/api/v1/config/institution")
async def institution():
    """Display and transfer settings for this institution (policies/institution.yaml)."""
    from pathlib import Path

    import yaml
    return {**yaml.safe_load((Path(__file__).resolve().parents[2] / "policies" / "institution.yaml").read_text()), "demo_mode": get_settings().demo_mode}


@app.get("/api/v1/config/runtime")
async def runtime():
    s = get_settings()
    last = next((e for e in reversed(events.recent()) if e["kind"] == "llm_call"), None)
    return {"route": s.llm_default_route, "model": s.route(s.llm_default_route)[1],
            "last_latency_s": last["duration_s"] if last else None}


def _dev_only():
    if not get_settings().dev_console:
        raise HTTPException(404)


@app.get("/api/v1/dev/events")
async def dev_events(limit: int = 200):
    _dev_only()
    return events.recent(limit)


@app.get("/api/v1/dev/model")
async def dev_model():
    _dev_only()
    s = get_settings()
    h = await build_provider(s).health()
    return h.__dict__


@app.get("/api/v1/dev/stream")
async def dev_stream():
    _dev_only()
    q = events.subscribe()

    async def gen():
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), 15)
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            events.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")
