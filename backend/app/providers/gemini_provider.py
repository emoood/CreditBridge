from copy import deepcopy
import json
import time
from typing import Any

from google import genai
from google.genai import types

from ..devlog import events
from .base import LLMProvider, LLMRequest, LLMResponse, ProviderHealth, ToolCall


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
    """Google Gemini API provider implementing LLMProvider protocol via google-genai."""

    def __init__(self, api_key: str, model: str = "gemini-3.8-flash",
                 temperature: float = 0.0, timeout: float = 120.0):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.name = f"gemini:{model}"
        self.client = genai.Client(api_key=api_key)
        self._content_cache: dict[str, types.Content] = {}

    async def health(self) -> ProviderHealth:
        if not self.api_key:
            return ProviderHealth(False, self.name, self.model, "Gemini API key is not configured")
        try:
            m = await self.client.aio.models.get(model=self.model)
            display = getattr(m, "display_name", None) or self.model
            return ProviderHealth(True, self.name, self.model, f"ok ({display})", loaded=True)
        except Exception as e:  # noqa: BLE001
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
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=m.get("content", ""))]))
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
                # Collect consecutive tool responses into a single user turn
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

        t0 = time.time()
        try:
            resp = await self.client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )
        except Exception as e:
            events.emit("error", where="llm_call", provider=self.name, model=self.model,
                        stage=request.stage, error=f"{type(e).__name__}: {e}", **request.meta)
            raise

        dur = round(time.time() - t0, 2)
        candidate = resp.candidates[0] if resp.candidates else None
        candidate_content = candidate.content if candidate else None

        calls: list[ToolCall] = []
        for fc in getattr(resp, "function_calls", None) or []:
            calls.append(ToolCall(id=getattr(fc, "id", "") or "", name=fc.name, arguments=dict(fc.args or {})))

        # Cache candidate content if it emitted tool calls so subsequent turns retain thought signatures
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
            provider=self.name,
            model=self.model,
            duration_s=dur,
            prompt_tokens=prompt_tokens,
            output_tokens=output_tokens,
            thinking="",
        )

        events.emit(
            "llm_call",
            provider=self.name,
            model=self.model,
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
