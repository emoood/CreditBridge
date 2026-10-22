"""Usage ledger (one row per model call) and the action trace. Both are persisted, per application and row, and shown in the
activity drawer. Nothing here stores document text."""
from typing import Any

from sqlalchemy import select

from ..db.models import TraceEvent, UsageEntry
from ..db.session import app_session
from ..providers.base import LLMProvider, LLMRequest, LLMResponse, ProviderHealth

COST_PER_MTOK_USD: dict[str, tuple[float, float]] = {}  # (input, output) per million tokens for hosted models; local models are free


class MeteredProvider:
    """Wraps a provider and writes one ledger row per call, tagged with the application and row being worked."""

    def __init__(self, inner: LLMProvider, app_id: str, row_id: str = ""):
        self.inner, self.app_id, self.row_id = inner, app_id, row_id
        self.name, self.model = getattr(inner, "name", ""), getattr(inner, "model", "")

    async def health(self) -> ProviderHealth:
        return await self.inner.health()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        resp = await self.inner.complete(request)
        pin, pout = COST_PER_MTOK_USD.get(resp.model, (0.0, 0.0))
        with app_session() as db:
            db.add(UsageEntry(app_id=self.app_id, row_id=self.row_id, stage=request.stage, provider=resp.provider, model=resp.model, seconds=resp.duration_s,
                              prompt_tokens=resp.prompt_tokens or 0, output_tokens=resp.output_tokens or 0,
                              cost_usd=((resp.prompt_tokens or 0) * pin + (resp.output_tokens or 0) * pout) / 1e6))
            db.commit()
        return resp


def trace(app_id: str, row_id: str, actor: str, name: str, args: dict[str, Any] | None = None, ok: bool = True, detail: str = "") -> None:
    with app_session() as db:
        db.add(TraceEvent(app_id=app_id, row_id=row_id, actor=actor, name=name, args=args or {}, ok=ok, detail=detail[:600]))
        db.commit()


def activity(app_id: str, limit: int = 300) -> dict[str, Any]:
    from ..db.models import iso
    with app_session() as db:
        calls = list(db.scalars(select(UsageEntry).where(UsageEntry.app_id == app_id).order_by(UsageEntry.id)))
        events = list(db.scalars(select(TraceEvent).where(TraceEvent.app_id == app_id).order_by(TraceEvent.id.desc()).limit(limit)))
        by_stage: dict[str, dict[str, float]] = {}
        for c in calls:
            s = by_stage.setdefault(c.stage or "other", {"calls": 0, "seconds": 0.0, "prompt_tokens": 0, "output_tokens": 0})
            s["calls"] += 1
            s["seconds"] = round(s["seconds"] + c.seconds, 2)
            s["prompt_tokens"] += c.prompt_tokens
            s["output_tokens"] += c.output_tokens
        recorded = [c for c in calls if (c.provider or "").startswith("recorded:")]
        return {"usage": {"recorded_calls": len(recorded), "recorded_seconds": round(sum(c.seconds for c in recorded), 1), "live_calls": len(calls) - len(recorded), "calls": len(calls), "seconds": round(sum(c.seconds for c in calls), 1), "prompt_tokens": sum(c.prompt_tokens for c in calls),
                          "output_tokens": sum(c.output_tokens for c in calls), "cost_usd": round(sum(c.cost_usd for c in calls), 4),
                          "models": sorted({c.model for c in calls if c.model}), "by_stage": by_stage},
                "trace": [{"at": iso(e.at), "row_id": e.row_id, "actor": e.actor, "name": e.name, "args": {k: v for k, v in e.args.items() if k != "_recorded"}, "ok": e.ok, "detail": e.detail,
                           "recorded": bool(e.args.get("_recorded"))} for e in reversed(events)]}
