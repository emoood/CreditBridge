import time

import httpx

from ..devlog import events
from .base import LLMRequest, LLMResponse, ProviderHealth, ToolCall


class OllamaProvider:
    """Ollama /api/chat. Structured `format` is unsupported on the MLX runner (see docs/decisions.md),
    so agents finish via a submit_result tool call instead."""

    def __init__(self, base_url: str, model: str, route: str, context: int = 8192,
                 temperature: float = 0.0, timeout: float = 120):
        self.base_url, self.model, self.route = base_url.rstrip("/"), model, route
        self.name = f"ollama:{route}"
        self.context, self.temperature, self.timeout = context, temperature, timeout

    async def health(self) -> ProviderHealth:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                tags = (await c.get(f"{self.base_url}/api/tags")).json()
                ps = (await c.get(f"{self.base_url}/api/ps")).json()
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(False, self.name, self.model, f"unreachable: {type(e).__name__}")
        if self.model not in {m["name"] for m in tags.get("models", [])}:
            return ProviderHealth(False, self.name, self.model, "model not pulled")
        loaded = next((m for m in ps.get("models", []) if m["name"] == self.model), None)
        return ProviderHealth(True, self.name, self.model, "ok", loaded=loaded is not None,
                              size_mb=round(loaded["size"] / 1e6) if loaded else None)

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body = {"model": self.model, "messages": request.messages, "stream": False, "think": False,
                "options": {"temperature": self.temperature, "num_ctx": self.context}}
        if request.tools:
            body["tools"] = request.tools
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(f"{self.base_url}/api/chat", json=body)
                r.raise_for_status()
                d = r.json()
        except Exception as e:
            events.emit("error", where="llm_call", provider=self.name, model=self.model,
                        stage=request.stage, error=f"{type(e).__name__}: {e}", **request.meta)
            raise
        dur = round(time.time() - t0, 2)
        m = d["message"]
        calls = [ToolCall(tc.get("id", ""), tc["function"]["name"], tc["function"].get("arguments", {}))
                 for tc in m.get("tool_calls") or []]
        resp = LLMResponse(m.get("content", ""), calls, self.name, self.model, dur,
                           d.get("prompt_eval_count"), d.get("eval_count"), m.get("thinking", ""))
        events.emit("llm_call", provider=self.name, model=self.model, stage=request.stage,
                    duration_s=dur, prompt_tokens=resp.prompt_tokens, output_tokens=resp.output_tokens,
                    tok_per_s=round(resp.output_tokens / dur, 1) if resp.output_tokens and dur else None,
                    tool_calls=[{"name": c.name, "arguments": c.arguments} for c in calls],
                    request_messages=request.messages, response_content=resp.content, **request.meta)
        return resp
