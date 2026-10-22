"""OpenAI-compatible chat provider (LM Studio, llama.cpp server, vLLM...). Same internal message format as the
Ollama provider; converted here to the OpenAI wire format (tool-call ids, JSON-string arguments)."""
import json
import time
from typing import Any

import httpx

from ..devlog import events
from .base import LLMRequest, LLMResponse, ProviderHealth, ToolCall


def to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Our assistant tool_calls carry dict arguments and tool results carry tool_name. OpenAI needs ids and JSON strings."""
    out: list[dict[str, Any]] = []
    pending: list[tuple[str, str]] = []  # (id, name) of tool calls awaiting a result
    n = 0
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                n += 1
                cid = f"call_{n}"
                fn = tc["function"]
                args = fn.get("arguments", {})
                calls.append({"id": cid, "type": "function",
                              "function": {"name": fn["name"], "arguments": args if isinstance(args, str) else json.dumps(args)}})
                pending.append((cid, fn["name"]))
            out.append({"role": "assistant", "content": m.get("content") or None, "tool_calls": calls})
        elif m["role"] == "tool":
            idx = next((i for i, (_, name) in enumerate(pending) if name == m.get("tool_name")), 0 if pending else None)
            cid = pending.pop(idx)[0] if idx is not None else f"call_{n + 1}"
            out.append({"role": "tool", "tool_call_id": cid, "content": m["content"]})
        else:
            out.append({"role": m["role"], "content": m.get("content", "")})
    return out


class OpenAICompatProvider:
    def __init__(self, base_url: str, model: str, route: str, temperature: float = 0.0, timeout: float = 180,
                 max_tokens: int = 2048):
        base = base_url.rstrip("/")
        self.base_url = base.removesuffix("/v1")  # accept URLs with or without /v1
        self.model, self.route = model, route
        self.name = f"openai-compat:{route}"
        self.temperature, self.timeout, self.max_tokens = temperature, timeout, max_tokens

    async def health(self) -> ProviderHealth:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                ids = {m["id"] for m in (await c.get(f"{self.base_url}/v1/models")).json().get("data", [])}
        except Exception as e:  # noqa: BLE001
            return ProviderHealth(False, self.name, self.model, f"unreachable: {type(e).__name__}")
        if self.model not in ids:
            return ProviderHealth(False, self.name, self.model, f"model not available (server lists {sorted(ids)[:4]})")
        return ProviderHealth(True, self.name, self.model, "ok")

    async def complete(self, request: LLMRequest) -> LLMResponse:
        body: dict[str, Any] = {"model": self.model, "messages": to_openai_messages(request.messages),
                                "temperature": self.temperature, "max_tokens": self.max_tokens, "stream": False}
        if request.tools:
            body["tools"] = request.tools
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                r = await c.post(f"{self.base_url}/v1/chat/completions", json=body)
                r.raise_for_status()
                d = r.json()
        except Exception as e:
            events.emit("error", where="llm_call", provider=self.name, model=self.model, stage=request.stage,
                        error=f"{type(e).__name__}: {e}", **request.meta)
            raise
        dur = round(time.time() - t0, 2)
        msg = d["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            raw = tc["function"].get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError:
                args = {"_unparsed": raw[:300]}  # malformed arguments surface as a validation error, not a crash
            calls.append(ToolCall(tc.get("id", ""), tc["function"]["name"], args))
        usage = d.get("usage") or {}
        resp = LLMResponse(msg.get("content") or "", calls, self.name, self.model, dur,
                           usage.get("prompt_tokens"), usage.get("completion_tokens"))
        events.emit("llm_call", provider=self.name, model=self.model, stage=request.stage, duration_s=dur,
                    prompt_tokens=resp.prompt_tokens, output_tokens=resp.output_tokens,
                    tok_per_s=round(resp.output_tokens / dur, 1) if resp.output_tokens and dur else None,
                    tool_calls=[{"name": c.name, "arguments": c.arguments} for c in calls],
                    request_messages=request.messages, response_content=resp.content, **request.meta)
        return resp
