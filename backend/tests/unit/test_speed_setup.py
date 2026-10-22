import pytest

from backend.app.config import Settings
from backend.app.providers.guard import GuardedProvider
from backend.app.providers.router import build_provider
from backend.app.providers.warmup import warm_up


def test_local_model_defaults_are_the_fast_ones():
    s = Settings(_env_file=None)
    assert s.llm_think is False and s.case_max_tool_rounds == 3 and s.ollama_keep_alive == "24h" and s.warmup_on_start
    p = build_provider(s)
    assert isinstance(p, GuardedProvider) and p.inner.think is False and p.inner.keep_alive == "24h"


@pytest.mark.asyncio
async def test_warm_up_never_raises_when_the_model_server_is_down():
    r = await warm_up(Settings(_env_file=None, ollama_mac_url="http://127.0.0.1:9"))
    assert r["ok"] is False and "error" in r
