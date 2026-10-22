"""Guard around any provider: (1) ONE model call at a time across the whole process, shared by the vision reader and both
agents, whichever job they belong to; (2) a hard wall-clock limit per call, one retry, then CallTimeout so the caller can
mark the step FAILED_RETRYABLE and move on. httpx timeouts bound each wait, not the whole call, so a hung or trickling
request could otherwise run for many minutes."""
import asyncio
import weakref

from ..devlog import events
from .base import LLMProvider, LLMRequest, LLMResponse, ProviderHealth

_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


def llm_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if loop not in _LOCKS:
        _LOCKS[loop] = asyncio.Lock()
    return _LOCKS[loop]


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
        async with llm_lock():  # waiting for the lock does not count against the call's time limit
            for attempt in range(self.retries + 1):
                try:
                    return await asyncio.wait_for(self.inner.complete(request), self.timeout)
                except TimeoutError:
                    events.emit("error", where="llm_timeout", provider=self.name, model=self.model, stage=request.stage,
                                attempt=attempt + 1, limit_s=self.timeout, **request.meta)
            raise CallTimeout(f"model call exceeded {self.timeout}s on {self.retries + 1} attempts ({request.stage})")
