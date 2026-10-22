"""Hard wall-clock limit around any provider call. httpx timeouts bound each wait, not the whole call, so a request
that keeps trickling (or a server that hangs) can run for many minutes. This cancels the call at `timeout` seconds,
retries once, then raises CallTimeout so the caller can mark the step FAILED_RETRYABLE and move on."""
import asyncio

from ..devlog import events
from .base import LLMProvider, LLMRequest, LLMResponse, ProviderHealth


class CallTimeout(Exception):
    """The model call exceeded the hard limit on every attempt."""


class GuardedProvider:
    def __init__(self, inner: LLMProvider, timeout: float, retries: int = 1):
        self.inner, self.timeout, self.retries = inner, timeout, retries

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def model(self) -> str:
        return self.inner.model

    async def health(self) -> ProviderHealth:
        return await self.inner.health()

    async def complete(self, request: LLMRequest) -> LLMResponse:
        for attempt in range(self.retries + 1):
            try:
                return await asyncio.wait_for(self.inner.complete(request), self.timeout)
            except TimeoutError:
                events.emit("error", where="llm_timeout", provider=self.name, model=self.model, stage=request.stage,
                            attempt=attempt + 1, limit_s=self.timeout, **request.meta)
        raise CallTimeout(f"model call exceeded {self.timeout}s on {self.retries + 1} attempts ({request.stage})")
