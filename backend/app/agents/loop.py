"""Bounded tool-calling loop. The model finishes by calling submit_result (Ollama's MLX runner has no
structured `format`; see docs/decisions.md). Every final result is validated locally with Pydantic."""
import json
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from ..config import get_settings
from ..devlog import events
from ..providers.base import LLMProvider, LLMRequest, ToolCall
from .json_action import action_schema, parse_action, protocol_prompt
from .tools.base import ToolContext, ToolRegistry

SUBMIT = "submit_result"


@dataclass
class AgentOutcome:
    status: str  # OK | NEEDS_HUMAN_REVIEW
    output: BaseModel | None = None
    error: str | None = None
    llm_calls: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


def _submit_tool(model: type[BaseModel]) -> dict:
    return {"type": "function", "function": {
        "name": SUBMIT, "description": "Submit your final structured result. Call exactly once to finish.",
        "parameters": model.model_json_schema()}}


def _try_content_json(content: str, model: type[BaseModel]) -> BaseModel | None:
    try:
        s, e = content.index("{"), content.rindex("}") + 1
        return model.model_validate(json.loads(content[s:e]))
    except (ValueError, ValidationError):
        return None


async def run_bounded_agent(provider: LLMProvider, registry: ToolRegistry, ctx: ToolContext, *, system: str,
                            user: str, final_model: type[BaseModel], max_tool_rounds: int, stage: str, max_seconds: float | None = None) -> AgentOutcome:
    """Investigate with tools; if the model cannot finish, one clean synthesis turn over a compact evidence summary.
    The budget is rounds AND seconds: once either is used up the agent may only submit (the seconds are checked between turns)."""
    evidence: list[str] = []
    json_mode = get_settings().agent_protocol == "json_action"
    run = _loop_json if json_mode else _loop
    ctx_deadline = time.time() + max_seconds if max_seconds else None
    ctx.deadline = ctx_deadline  # tools may look at it too (a long vision call should not start after the deadline)
    out = await run(provider, registry, ctx, system, user, final_model, max_tool_rounds, stage, evidence)
    if out.status == "OK" or not evidence:
        return out
    meta = {"course": ctx.source_course_id}
    summary = "\n".join(evidence)[-7000:]
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": f"{user}\n\nEvidence you already gathered (tool results):\n{summary}\n\n"
                                            "Do not call any other tool. Call submit_result now with your best conclusion from this "
                                            "evidence. Use NEEDS_MORE_EVIDENCE only if the evidence truly cannot support a conclusion."}]
    out.llm_calls += 1
    if json_mode:
        messages[0]["content"] += protocol_prompt([])
        schema = action_schema(registry.visible_tools(ctx.role), final_model, submit_only=True)
        resp = await provider.complete(LLMRequest(messages, [], stage + "_synthesis", meta, response_format=schema))
        act = parse_action(resp.content)
        sub = ToolCall("", SUBMIT, act[1]) if act and act[0] == "submit" else None
    else:
        resp = await provider.complete(LLMRequest(messages, [_submit_tool(final_model)], stage + "_synthesis", meta))
        sub = next((c for c in resp.tool_calls if c.name == SUBMIT), None)
    try:
        if sub:
            out.output = final_model.model_validate(sub.arguments)
        else:
            out.output = _try_content_json(resp.content, final_model)
    except ValidationError:
        out.output = None
    if out.output is not None:
        out.status, out.error = "OK", None
        out.flags.append("SYNTHESIS_FALLBACK")
    return out


def _over(ctx: ToolContext, out: AgentOutcome) -> bool:
    d = getattr(ctx, "deadline", None)
    if d is not None and time.time() > d:
        if "TIME_BUDGET_USED" not in out.flags:
            out.flags.append("TIME_BUDGET_USED")
        return True
    return False


async def _loop(provider: LLMProvider, registry: ToolRegistry, ctx: ToolContext, system: str, user: str,
                final_model: type[BaseModel], max_tool_rounds: int, stage: str, evidence: list[str]) -> AgentOutcome:
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    out = AgentOutcome("NEEDS_HUMAN_REVIEW")
    meta = {"course": ctx.source_course_id}
    submit = _submit_tool(final_model)
    repaired = False
    rounds = 0
    total_turns = 0

    async def call(tools):
        out.llm_calls += 1
        return await provider.complete(LLMRequest(messages, tools, stage, meta))

    while True:
        final_turn = rounds >= max_tool_rounds or _over(ctx, out)
        tools = [submit] if final_turn else registry.visible_tools(ctx.role) + [submit]
        resp = await call(tools)
        sub = next((c for c in resp.tool_calls if c.name == SUBMIT), None)
        if sub:
            try:
                out.output, out.status = final_model.model_validate(sub.arguments), "OK"
                return out
            except ValidationError as e:
                err = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
                events.emit("error", where="schema_validation", stage=stage, error=err, **meta)
                if repaired:
                    out.error = f"INVALID_OUTPUT: {err}"
                    return out
                repaired = True
                messages += [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": SUBMIT, "arguments": sub.arguments}}]},
                             {"role": "tool", "tool_name": SUBMIT, "content": json.dumps({"ok": False, "error": err})}]
                out.flags.append("REPAIR_ATTEMPTED")
                continue
        if not resp.tool_calls:
            parsed = _try_content_json(resp.content, final_model)
            if parsed:
                out.output, out.status = parsed, "OK"
                out.flags.append("FINAL_FROM_CONTENT")
                return out
            if repaired:
                out.error = "NO_VALID_FINAL_RESULT"
                return out
            repaired = True
            out.flags.append("REPAIR_ATTEMPTED")
            messages += [{"role": "assistant", "content": resp.content},
                         {"role": "user", "content": "Finish now by calling submit_result with the required fields."}]
            continue
        if final_turn:  # budget exhausted but the model still wants tools: one firm nudge, then fail safely
            if repaired:
                out.error = "TOOL_BUDGET_EXCEEDED"
                return out
            repaired = True
            out.flags.append("FORCED_FINAL_NUDGE")
            messages += [{"role": "assistant", "content": resp.content or ""},
                         {"role": "user", "content": "The tool budget is used up. Call submit_result NOW with your best conclusion "
                                                      "from what you have already read. Use NEEDS_MORE_EVIDENCE if you are unsure."}]
            continue
        if any(c.name != "add_case_note" for c in resp.tool_calls):  # note-taking is free; investigating is budgeted
            rounds += 1
        total_turns += 1
        if total_turns > 3 * max_tool_rounds + 5:
            out.error = "TOOL_BUDGET_EXCEEDED"
            return out
        messages.append({"role": "assistant", "content": resp.content or "",
                         "tool_calls": [{"function": {"name": c.name, "arguments": c.arguments}} for c in resp.tool_calls]})
        for c in resp.tool_calls:
            r = await registry.execute(c, ctx)
            out.tool_calls.append({"name": c.name, "arguments": c.arguments, "ok": r.ok})
            if r.ok and c.name != "add_case_note":
                evidence.append(f"[{c.name} {json.dumps(c.arguments, ensure_ascii=False)[:120]}] {r.to_message()[:1500]}")
            messages.append({"role": "tool", "tool_name": c.name, "content": r.to_message()})


async def _loop_json(provider: LLMProvider, registry: ToolRegistry, ctx: ToolContext, system: str, user: str,
                     final_model: type[BaseModel], max_tool_rounds: int, stage: str, evidence: list[str]) -> AgentOutcome:
    """JSON-action protocol: one constrained JSON action per turn; our code executes tools (no runtime tool-call parser)."""
    tools = registry.visible_tools(ctx.role)
    full, final_only = action_schema(tools, final_model), action_schema(tools, final_model, submit_only=True)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system + protocol_prompt(tools)}, {"role": "user", "content": user}]
    out = AgentOutcome("NEEDS_HUMAN_REVIEW")
    meta = {"course": ctx.source_course_id}
    repaired, rounds, total_turns = False, 0, 0
    while True:
        final_turn = rounds >= max_tool_rounds or _over(ctx, out)
        out.llm_calls += 1
        resp = await provider.complete(LLMRequest(messages, [], stage, meta, response_format=final_only if final_turn else full))
        act = parse_action(resp.content)
        if act is None:
            if repaired:
                out.error = "NO_VALID_FINAL_RESULT"
                return out
            repaired = True
            out.flags.append("REPAIR_ATTEMPTED")
            messages += [{"role": "assistant", "content": resp.content},
                         {"role": "user", "content": "Reply with exactly one JSON object: {\"thought\":..., \"action\":{...}}."}]
            continue
        kind, payload, _thought = act
        if kind == "submit":
            try:
                out.output, out.status = final_model.model_validate(payload), "OK"
                return out
            except ValidationError as e:
                err = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
                events.emit("error", where="schema_validation", stage=stage, error=err, **meta)
                if repaired:
                    out.error = f"INVALID_OUTPUT: {err}"
                    return out
                repaired = True
                out.flags.append("REPAIR_ATTEMPTED")
                messages += [{"role": "assistant", "content": resp.content},
                             {"role": "user", "content": f"Your result was invalid ({err}). Submit again with valid fields."}]
                continue
        if final_turn:  # budget used up but the model still wants a tool (the final-only schema normally prevents this)
            out.error = "TOOL_BUDGET_EXCEEDED"
            return out
        name, args = payload["tool"], payload["arguments"]
        r = await registry.execute(ToolCall("", name, args), ctx)
        out.tool_calls.append({"name": name, "arguments": args, "ok": r.ok})
        if r.ok and name != "add_case_note":
            evidence.append(f"[{name} {json.dumps(args, ensure_ascii=False)[:120]}] {r.to_message()[:1500]}")
        if name != "add_case_note":
            rounds += 1
        total_turns += 1
        if total_turns > 3 * max_tool_rounds + 5:
            out.error = "TOOL_BUDGET_EXCEEDED"
            return out
        messages += [{"role": "assistant", "content": resp.content}, {"role": "user", "content": f"TOOL RESULT for {name}: {r.to_message()}"}]
