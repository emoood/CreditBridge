"""JSON-action protocol: instead of the runtime's native tool-call parser, the model returns ONE structured action per
turn, constrained by a JSON schema that lists only the real tools and their argument types:
    {"thought": "...", "action": {"type": "call_tool", "tool": "<name>", "arguments": {...}}}
    {"thought": "...", "action": {"type": "submit", "result": {...final schema...}}}
Our code parses, validates and executes. `thought` comes first so the model reasons before it commits."""
import json
from typing import Any

from pydantic import BaseModel

THOUGHT_MAX = 300


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve $ref/$defs and drop titles so strict grammar-based servers accept the schema."""
    defs = schema.get("$defs", {})

    def walk(n: Any) -> Any:
        if isinstance(n, dict):
            if "$ref" in n:
                return walk(defs[n["$ref"].split("/")[-1]])
            return {k: walk(v) for k, v in n.items() if k not in ("$defs", "title")}
        if isinstance(n, list):
            return [walk(x) for x in n]
        return n
    return walk(schema)


def action_schema(tools: list[dict[str, Any]], final_model: type[BaseModel], submit_only: bool = False) -> dict[str, Any]:
    submit = {"type": "object", "properties": {"type": {"const": "submit"}, "result": inline_refs(final_model.model_json_schema())},
              "required": ["type", "result"], "additionalProperties": False}
    variants = [submit]
    if not submit_only:
        for t in tools:
            fn = t["function"]
            variants.append({"type": "object", "properties": {"type": {"const": "call_tool"}, "tool": {"const": fn["name"]},
                                                              "arguments": inline_refs(fn["parameters"])},
                             "required": ["type", "tool", "arguments"], "additionalProperties": False})
    return {"type": "object", "properties": {"thought": {"type": "string", "maxLength": THOUGHT_MAX}, "action": {"anyOf": variants}},
            "required": ["thought", "action"], "additionalProperties": False}


def protocol_prompt(tools: list[dict[str, Any]]) -> str:
    head = ("OUTPUT PROTOCOL: every reply must be exactly one JSON object with a short `thought` (your reasoning, under "
            f"{THOUGHT_MAX} characters) and one `action`. To use a tool: {{\"type\":\"call_tool\",\"tool\":NAME,\"arguments\":{{...}}}}. "
            "To finish: {\"type\":\"submit\",\"result\":{...}} (call submit exactly once). Tool results arrive in the next message.")
    lines = ["", head, "Tools:"]
    for t in tools:
        fn = t["function"]
        props = ", ".join(f"{k}: {v.get('type', 'any')}" for k, v in inline_refs(fn["parameters"]).get("properties", {}).items())
        lines.append(f"- {fn['name']}({props}): {fn['description']}")
    return "\n".join(lines)


def parse_action(text: str) -> tuple[str, dict[str, Any], str] | None:
    """Return (kind, payload, thought) or None. kind is 'call_tool' or 'submit'."""
    try:
        s, e = text.index("{"), text.rindex("}") + 1
        d = json.loads(text[s:e])
        a = d["action"]
        if a["type"] == "call_tool":
            return "call_tool", {"tool": a["tool"], "arguments": a.get("arguments") or {}}, d.get("thought", "")
        if a["type"] == "submit":
            return "submit", a["result"], d.get("thought", "")
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return None
