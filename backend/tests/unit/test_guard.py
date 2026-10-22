import asyncio
import time

import pytest

from backend.app.providers.base import LLMRequest, LLMResponse
from backend.app.providers.guard import CallTimeout, GuardedProvider


class Hangs:
    name, model = "hang", "m"

    def __init__(self, hang_first: int):
        self.calls, self.hang_first = 0, hang_first

    async def complete(self, request):
        self.calls += 1
        if self.calls <= self.hang_first:
            await asyncio.sleep(3600)  # a hung request
        return LLMResponse("ok", [], self.name, self.model, 0.0)


async def test_hung_call_is_cut_off_retried_once_then_raises():
    inner = Hangs(hang_first=99)
    t0 = time.time()
    with pytest.raises(CallTimeout):
        await GuardedProvider(inner, timeout=0.2, retries=1).complete(LLMRequest([], stage="matching"))
    assert inner.calls == 2 and time.time() - t0 < 2  # two attempts of 0.2 s, not an hour


async def test_one_hang_then_success_is_recovered_by_the_retry():
    inner = Hangs(hang_first=1)
    r = await GuardedProvider(inner, timeout=0.2, retries=1).complete(LLMRequest([], stage="matching"))
    assert r.content == "ok" and inner.calls == 2
