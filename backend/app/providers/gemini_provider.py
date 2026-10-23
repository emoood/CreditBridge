import asyncio
import base64
from copy import deepcopy
import json
import time
from typing import Any

from google import genai
from google.genai import types

from ..devlog import events
from .base import LLMProvider, LLMRequest, LLMResponse, ProviderHealth, ToolCall
from .rate_limiter import RateLimiter


def clean_schema_for_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve $defs/$ref and remove fields unsupported by Gemini's schema specification."""
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, list):
            return [resolve(x) for x in node]
        if not isinstance(node, dict):
            return node

        # If it has a $ref, resolve it against $defs
        if "$ref" in node:
            ref = node["$ref"]
            ref_name = ref.split("/")[-1]
            if ref_name in defs:
                resolved_def = deepcopy(defs[ref_name])
                for k, v in node.items():
                    if k != "$ref":
                        resolved_def[k] = v
                return resolve(resolved_def)

        cleaned: dict[str, Any] = {}
        for k, v in node.items():
            if k in ("$defs", "$ref", "title", "additionalProperties", "$schema"):
                continue
            cleaned[k] = resolve(v)
        return cleaned

    return resolve(schema)


class GeminiProvider:
    """Google Gemini API provider implementing LLMProvider protocol with rate limiting and retry."""

    def __init__(self, api_key: str, model: str = "gemini-3.5-flash",
                 temperature: float = 0.0, timeout: float = 120.0, rpm: int = 5):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.rpm = rpm
        self.name = f"gemini:{model}"
        self.client = genai.Client(api_key=api_key)
        self.rate_limiter = RateLimiter(rpm=rpm)
        self._content_cache: dict[str, types.Content] = {}

    async def health(self) -> ProviderHealth:
        if not self.api_key:
            return ProviderHealth(False, self.name, self.model, "Gemini API key is not configured")
        try:
            m = await self.client.aio.models.get(model=self.model)
            display = getattr(m, "display_name", None) or self.model
            return ProviderHealth(True, self.name, self.model, f"ok ({display})", loaded=True)
        except Exception as e:  # noqa: BLE001
            # Try fallback model if configured model fails
            if self.model != "gemini-3.5-flash-lite":
                try:
                    m = await self.client.aio.models.get(model="gemini-3.5-flash-lite")
                    return ProviderHealth(True, f"gemini:gemini-3.5-flash-lite", "gemini-3.5-flash-lite", f"ok ({m.display_name})", loaded=True)
                except Exception:
                    pass
            return ProviderHealth(False, self.name, self.model, f"unreachable: {type(e).__name__}: {e}")

    def _convert_tools(self, tools: list[dict[str, Any]]) -> list[types.Tool]:
        if not tools:
            return []
        declarations: list[types.FunctionDeclaration] = []
        for t in tools:
            fn = t.get("function", t)
            raw_params = fn.get("parameters", {})
            cleaned_params = clean_schema_for_gemini(raw_params)
            declarations.append(
                types.FunctionDeclaration(
                    name=fn["name"],
                    description=fn.get("description", ""),
                    parameters=cleaned_params,
                )
            )
        return [types.Tool(function_declarations=declarations)]

    def _build_contents(self, messages: list[dict[str, Any]]) -> list[types.Content]:
        contents: list[types.Content] = []
        i = 0
        while i < len(messages):
            m = messages[i]
            role = m.get("role")
            if role == "system":
                i += 1
                continue
            elif role == "user":
                parts: list[types.Part] = []
                content_text = m.get("content")
                if content_text:
                    parts.append(types.Part.from_text(text=content_text))
                for img_b64 in m.get("images") or []:
                    try:
                        img_bytes = base64.b64decode(img_b64)
                        parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))
                    except Exception:
                        pass
                if not parts:
                    parts.append(types.Part.from_text(text=""))
                contents.append(types.Content(role="user", parts=parts))
                i += 1
            elif role == "assistant":
                tool_calls = m.get("tool_calls")
                if tool_calls:
                    tcs = [
                        {"name": tc["function"]["name"], "arguments": tc["function"].get("arguments", {})}
                        for tc in tool_calls
                    ]
                    cache_key = json.dumps(tcs, sort_keys=True)
                    if cache_key in self._content_cache:
                        contents.append(self._content_cache[cache_key])
                    else:
                        parts: list[types.Part] = []
                        if m.get("content"):
                            parts.append(types.Part.from_text(text=m["content"]))
                        for tc in tool_calls:
                            fn = tc["function"]
                            parts.append(types.Part.from_function_call(name=fn["name"], args=fn.get("arguments", {})))
                        contents.append(types.Content(role="model", parts=parts))
                else:
                    contents.append(types.Content(role="model", parts=[types.Part.from_text(text=m.get("content", ""))]))
                i += 1
            elif role == "tool":
                tool_parts: list[types.Part] = []
                while i < len(messages) and messages[i].get("role") == "tool":
                    tm = messages[i]
                    c = tm.get("content", "")
                    try:
                        resp_data = json.loads(c) if isinstance(c, str) and c.strip().startswith(("{", "[")) else {"result": c}
                    except Exception:
                        resp_data = {"result": c}
                    tool_parts.append(types.Part.from_function_response(name=tm["tool_name"], response={"result": resp_data}))
                    i += 1
                contents.append(types.Content(role="user", parts=tool_parts))
            else:
                i += 1
        return contents

    async def complete(self, request: LLMRequest) -> LLMResponse:
        system_instruction: str | None = None
        for m in request.messages:
            if m.get("role") == "system":
                system_instruction = m.get("content")
                break

        contents = self._build_contents(request.messages)
        tools = self._convert_tools(request.tools)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=self.temperature,
            tools=tools or None,
        )

        models_to_try = [self.model]
        if self.model not in ("gemini-3.5-flash-lite", "gemini-3.5-flash"):
            models_to_try.append("gemini-3.5-flash-lite")

        resp = None
        used_model = self.model
        t0 = time.time()

        for m_idx, current_model in enumerate(models_to_try):
            for attempt in range(4):
                # Rate limit guard: wait until slot is available before making HTTP call
                waited = await self.rate_limiter.acquire()
                if waited > 0.5:
                    events.emit("info", where="rate_limit", model=current_model, waited_s=waited, **request.meta)

                try:
                    resp = await self.client.aio.models.generate_content(
                        model=current_model,
                        contents=contents,
                        config=config,
                    )
                    used_model = current_model
                    break
                except Exception as e:
                    err_str = str(e)
                    is_daily_quota = ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str) and "PerDay" in err_str
                    is_last_model = (m_idx == len(models_to_try) - 1)
                    if is_daily_quota and not is_last_model:
                        break  # Immediately try fallback model
                    if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "503" in err_str or "UNAVAILABLE" in err_str) and attempt < 3:
                        delay = RateLimiter.extract_retry_delay(e)
                        self.rate_limiter.penalize(delay)
                        await asyncio.sleep(delay)
                        continue
                    if is_last_model and attempt == 3:
                        events.emit("error", where="llm_call", provider=self.name, model=current_model,
                                    stage=request.stage, error=f"{type(e).__name__}: {e}", **request.meta)
                        raise
            if resp is not None:
                break

        if resp is None:
            raise RuntimeError(f"Failed to generate content across candidate models: {models_to_try}")

        dur = round(time.time() - t0, 2)
        candidate = resp.candidates[0] if resp.candidates else None
        candidate_content = candidate.content if candidate else None

        calls: list[ToolCall] = []
        for fc in getattr(resp, "function_calls", None) or []:
            calls.append(ToolCall(id=getattr(fc, "id", "") or "", name=fc.name, arguments=dict(fc.args or {})))

        if calls and candidate_content:
            tcs = [{"name": c.name, "arguments": c.arguments} for c in calls]
            self._content_cache[json.dumps(tcs, sort_keys=True)] = candidate_content

        text_content = resp.text or ""
        usage = getattr(resp, "usage_metadata", None)
        prompt_tokens = usage.prompt_token_count if usage else None
        output_tokens = usage.candidates_token_count if usage else None

        llm_resp = LLMResponse(
            content=text_content,
            tool_calls=calls,
            provider=f"gemini:{used_model}",
            model=used_model,
            duration_s=dur,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            thinking="",
        )

        events.emit(
            "llm_call",
            provider=llm_resp.provider,
            model=llm_resp.model,
            stage=request.stage,
            duration_s=dur,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            tok_per_s=round(output_tokens / dur, 1) if output_tokens and dur else None,
            tool_calls=[{"name": c.name, "arguments": c.arguments} for c in calls],
            request_messages=request.messages,
            response_content=llm_resp.content,
            **request.meta,
        )
        return llm_resp
