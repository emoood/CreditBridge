from typing import Any
import pytest
from pydantic import BaseModel, Field

from backend.app.domain.schemas import MatchOutput, ReviewOutput
from backend.app.providers.base import LLMRequest, ToolCall
from backend.app.providers.gemini_provider import GeminiProvider, clean_schema_for_gemini


def test_clean_schema_for_gemini_resolves_defs_and_removes_unsupported():
    class Item(BaseModel):
        name: str = Field(description="item name")
        score: int

    class Container(BaseModel):
        items: list[Item]
        tag: str

    schema = Container.model_json_schema()
    assert "$defs" in schema

    cleaned = clean_schema_for_gemini(schema)
    assert "$defs" not in cleaned
    assert "$ref" not in str(cleaned)
    assert "additionalProperties" not in str(cleaned)
    assert "title" not in cleaned
    # Items should be inlined
    assert "properties" in cleaned["properties"]["items"]["items"]
    assert "name" in cleaned["properties"]["items"]["items"]["properties"]


def test_clean_schema_domain_models():
    for model in [MatchOutput, ReviewOutput]:
        raw = model.model_json_schema()
        cleaned = clean_schema_for_gemini(raw)
        assert "$defs" not in cleaned
        assert "$ref" not in str(cleaned)
        assert "additionalProperties" not in str(cleaned)
        assert "title" not in cleaned


def test_gemini_provider_tool_conversion():
    prov = GeminiProvider(api_key="fake-key", model="gemini-3.8-flash")
    raw_tool = {
        "type": "function",
        "function": {
            "name": "lookup_course",
            "description": "Lookup course by code",
            "parameters": {
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
        },
    }
    tools = prov._convert_tools([raw_tool])
    assert len(tools) == 1
    fd = tools[0].function_declarations[0]
    assert fd.name == "lookup_course"


def test_gemini_provider_content_building():
    prov = GeminiProvider(api_key="fake-key", model="gemini-3.8-flash")
    messages = [
        {"role": "system", "content": "You are a test assistant"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Let me check", "tool_calls": [{"function": {"name": "test_fn", "arguments": {"x": 1}}}]},
        {"role": "tool", "tool_name": "test_fn", "content": '{"ok": true}'},
    ]
    contents = prov._build_contents(messages)
    # System instruction is skipped from contents
    assert len(contents) == 3
    assert contents[0].role == "user"
    assert contents[1].role == "model"
    assert contents[2].role == "user"


def test_gemini_provider_multimodal_images():
    prov = GeminiProvider(api_key="fake-key", model="gemini-3.5-flash-lite")
    b64_img = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    messages = [
        {"role": "user", "content": "Read this transcript block.", "images": [b64_img]},
    ]
    contents = prov._build_contents(messages)
    assert len(contents) == 1
    assert contents[0].role == "user"
    assert len(contents[0].parts) == 2
    assert contents[0].parts[0].text == "Read this transcript block."
    assert contents[0].parts[1].inline_data is not None
    assert contents[0].parts[1].inline_data.mime_type == "image/png"
