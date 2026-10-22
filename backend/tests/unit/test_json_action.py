import json

import pytest

from backend.app.agents.json_action import action_schema, inline_refs, parse_action
from backend.app.agents.matching_agent import run_matching
from backend.app.agents.tools.base import ToolContext, ToolRegistry
from backend.app.agents.tools.evidence_tools import ALL_TOOLS
from backend.app.config import get_settings
from backend.app.domain.schemas import MatchOutput
from backend.app.providers.base import LLMResponse
from backend.app.providers.fake import FakeLLMProvider
from backend.app.services.catalog_retriever import CatalogRetriever
from backend.app.services.demo_repo import DemoRepo

REG = ToolRegistry(ALL_TOOLS)


def act(kind, **kw):
    a = {"type": "call_tool", **kw} if kind == "call_tool" else {"type": "submit", **kw}
    return json.dumps({"thought": "t", "action": a})


def test_schema_is_self_contained_and_lists_every_tool():
    s = action_schema(REG.visible_tools("matcher"), MatchOutput)
    text = json.dumps(s)
    assert "$ref" not in text and "$defs" not in text
    variants = s["properties"]["action"]["anyOf"]
    names = {v["properties"].get("tool", {}).get("const") for v in variants} - {None}
    assert names == {t["function"]["name"] for t in REG.visible_tools("matcher")}
    assert s["required"] == ["thought", "action"] and next(iter(s["properties"])) == "thought"  # reasoning before the decision
    assert len(action_schema(REG.visible_tools("matcher"), MatchOutput, submit_only=True)["properties"]["action"]["anyOf"]) == 1


def test_inline_refs_resolves_nested_models():
    assert "supported_outcomes" in inline_refs(MatchOutput.model_json_schema())["properties"]


def test_parse_action():
    assert parse_action(act("call_tool", tool="get_policy_summary", arguments={}))[0] == "call_tool"
    assert parse_action("noise " + act("submit", result={"recommendation": "NO_DEFENSIBLE_MATCH"}) + " tail")[0] == "submit"
    assert parse_action("not json") is None and parse_action('{"action": {"type": "dance"}}') is None


@pytest.fixture
def json_mode(monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_protocol", "json_action")


async def test_full_investigation_in_json_protocol(json_mode):
    repo = DemoRepo()
    ctx = ToolContext(repo, CatalogRetriever(list(repo.targets.values())), "matcher", "COMP101")
    src = repo.sources["COMP101"].excerpt_ids[0]
    p = FakeLLMProvider([
        LLMResponse(act("call_tool", tool="get_target_course", arguments={"course_id": "CS101"}), [], "f", "f", 0),
        LLMResponse(act("call_tool", tool="nonexistent_tool", arguments={}), [], "f", "f", 0),   # unknown tool -> error result, loop goes on
        LLMResponse(act("submit", result={"recommendation": "POTENTIAL_MATCH", "target_course_id": "CS101", "supported_outcomes": [
            {"source_excerpt_id": src, "target_excerpt_id": "tgt-CS101-o1", "explanation": "same"}]}), [], "f", "f", 0)])
    r = await run_matching(p, REG, ctx)
    assert r.status == "POTENTIAL_MATCH" and r.agent.llm_calls == 3
    assert [t["ok"] for t in r.agent.tool_calls] == [True, False]
    assert p.requests[0].response_format is not None and p.requests[0].tools == []  # no native tool calls in this mode
    assert any("TOOL RESULT for get_target_course" in m["content"] for m in p.requests[-1].messages)


async def test_json_protocol_keeps_the_referee_checks(json_mode):
    repo = DemoRepo()
    ctx = ToolContext(repo, CatalogRetriever(list(repo.targets.values())), "matcher", "COMP101")
    bad = act("submit", result={"recommendation": "POTENTIAL_MATCH", "target_course_id": "CS101", "supported_outcomes": [
        {"source_excerpt_id": "src-fake", "target_excerpt_id": "tgt-fake", "explanation": "x"}]})
    r = await run_matching(FakeLLMProvider([LLMResponse(bad, [], "f", "f", 0)]), REG, ctx)
    assert r.status == "NEEDS_MORE_EVIDENCE" and "TARGET_NEVER_READ" in r.flags  # never read the course -> downgraded
