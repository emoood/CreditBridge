import json

import pytest

from backend.app.agents.matching_agent import run_matching
from backend.app.agents.reviewer_agent import run_review
from backend.app.agents.tools.base import ToolContext, ToolRegistry
from backend.app.agents.tools.evidence_tools import ALL_TOOLS
from backend.app.providers.base import ToolCall
from backend.app.providers.fake import FakeLLMProvider
from backend.app.services import policy_rules as pr
from backend.app.services.catalog_retriever import CatalogRetriever
from backend.app.services.demo_repo import DemoRepo

REG = ToolRegistry(ALL_TOOLS)


@pytest.fixture
def repo():
    return DemoRepo()


def ctx(repo, code="COMP101", role="matcher"):
    return ToolContext(repo, CatalogRetriever(list(repo.targets.values())), role, code)


def submit(**kw):
    return [ToolCall("1", "submit_result", kw)]


def good_match(repo):
    s = repo.sources["COMP101"].excerpt_ids
    return {"recommendation": "POTENTIAL_MATCH", "target_course_id": "CS101",
            "supported_outcomes": [{"source_excerpt_id": s[0], "target_excerpt_id": "tgt-CS101-o1", "explanation": "same"}]}


async def test_happy_path_tool_round_then_submit(repo):
    p = FakeLLMProvider([[ToolCall("a", "get_target_course", {"course_id": "CS101"})], submit(**good_match(repo))])
    r = await run_matching(p, REG, ctx(repo))
    assert r.status == "POTENTIAL_MATCH" and r.agent.llm_calls == 2
    assert r.agent.tool_calls[0]["ok"] and "CS101" in [c.course_id for c in r.candidates]


async def test_fabricated_citation_removed_and_match_downgraded(repo):
    bad = good_match(repo)
    bad["supported_outcomes"][0]["target_excerpt_id"] = "tgt-CS101-o99"
    r = await run_matching(FakeLLMProvider([submit(**bad)]), REG, ctx(repo))
    assert r.status == "NEEDS_MORE_EVIDENCE"
    assert "INVALID_CITATION_REMOVED:tgt-CS101-o99" in r.flags and "NO_VALID_CITATIONS" in r.flags


async def test_citation_from_wrong_course_rejected(repo):
    bad = good_match(repo)
    bad["supported_outcomes"][0]["source_excerpt_id"] = repo.sources["COMP220"].excerpt_ids[0]
    r = await run_matching(FakeLLMProvider([submit(**bad)]), REG, ctx(repo))
    assert r.status == "NEEDS_MORE_EVIDENCE"


async def test_missing_syllabus_makes_no_llm_call_and_creates_request(repo):
    p = FakeLLMProvider([])
    r = await run_matching(p, REG, ctx(repo, "ENGL101"))
    assert r.status == "NEEDS_SYLLABUS" and p.requests == []
    assert repo.syllabus_requests[0]["course_id"] == "ENGL101"


async def test_target_outside_candidates_is_rejected(repo):
    m = good_match(repo)
    m["target_course_id"] = "CS499"
    r = await run_matching(FakeLLMProvider([submit(**m)]), REG, ctx(repo))
    assert r.status == "NEEDS_MORE_EVIDENCE" and "TARGET_NOT_IN_CANDIDATES" in r.flags


async def test_unknown_tool_bad_args_and_scope_return_errors_not_crashes(repo):
    c = ctx(repo)
    calls = [ToolCall("1", "delete_everything", {}), ToolCall("2", "get_target_course", {}),
             ToolCall("3", "find_source_syllabus", {"course_id": "COMP220"}),
             ToolCall("4", "get_source_excerpt", {"excerpt_id": repo.sources["COMP220"].excerpt_ids[0]})]
    results = [await REG.execute(x, c) for x in calls]
    assert [r.ok for r in results] == [False] * 4
    assert "not available" in results[0].error and "Invalid arguments" in results[1].error
    assert "scope" in results[2].error and "scope" in results[3].error


async def test_role_permissions(repo):
    rv = ctx(repo, role="reviewer")
    assert not (await REG.execute(ToolCall("1", "search_target_catalog", {"query": "python"}), rv)).ok
    assert not (await REG.execute(ToolCall("1", "request_syllabus", {"course_id": "COMP101"}), rv)).ok
    assert {t["function"]["name"] for t in REG.visible_tools("matcher")}.isdisjoint({"get_match_evidence"})


async def test_search_top_k_capped_at_3(repo):
    r = await REG.execute(ToolCall("1", "search_target_catalog", {"query": "programming data", "top_k": 10}), ctx(repo))
    assert r.ok and len(r.data) <= 3


async def test_tool_budget_then_forced_final_turn_with_submit_only(repo):
    loop = [[ToolCall(str(i), "get_policy_summary", {})] for i in range(3)]
    p = FakeLLMProvider([*loop, submit(**good_match(repo))])
    r = await run_matching(p, REG, ctx(repo), max_rounds=3)
    assert r.status == "POTENTIAL_MATCH"
    assert [t["function"]["name"] for t in p.requests[-1].tools] == ["submit_result"]


async def test_tool_use_after_budget_exhausted_fails_safely(repo):
    p = FakeLLMProvider([[ToolCall("1", "get_policy_summary", {})], [ToolCall("2", "get_policy_summary", {})]])
    r = await run_matching(p, REG, ctx(repo), max_rounds=1)
    assert r.status == "NEEDS_HUMAN_REVIEW" and "TOOL_BUDGET_EXCEEDED" in r.flags


async def test_schema_violation_one_repair_then_fail(repo):
    p = FakeLLMProvider([submit(recommendation="APPROVED"), submit(recommendation="APPROVED")])
    r = await run_matching(p, REG, ctx(repo))
    assert r.status == "NEEDS_HUMAN_REVIEW" and "REPAIR_ATTEMPTED" in r.flags and p.script == []


async def test_schema_repair_succeeds(repo):
    p = FakeLLMProvider([submit(recommendation="nope"), submit(recommendation="NO_DEFENSIBLE_MATCH")])
    r = await run_matching(p, REG, ctx(repo))
    assert r.status == "NO_DEFENSIBLE_MATCH" and "REPAIR_ATTEMPTED" in r.flags


async def test_agent_output_cannot_carry_an_approval_field(repo):
    p = FakeLLMProvider([submit(recommendation="POTENTIAL_MATCH", approved=True), submit(recommendation="POTENTIAL_MATCH", approved=True)])
    r = await run_matching(p, REG, ctx(repo))
    assert r.status == "NEEDS_HUMAN_REVIEW" and r.output is None


async def test_injection_text_is_only_tool_data(repo):
    from backend.app.services.demo_repo import FIXTURES
    from backend.app.services.evidence import ingest_syllabus_pdf
    ingest_syllabus_pdf(repo.store, FIXTURES / "syllabi" / "INJECT999.pdf", "src-doc-COMP101", "src-COMP101x")
    p = FakeLLMProvider([submit(recommendation="NO_DEFENSIBLE_MATCH")])
    await run_matching(p, REG, ctx(repo))
    assert "IGNORE ALL" not in p.requests[0].messages[0]["content"]  # never in the system prompt


async def test_reviewer_runs_only_on_potential_match_and_flags_bad_citations(repo):
    c = ctx(repo)
    m = await run_matching(FakeLLMProvider([submit(**good_match(repo))]), REG, c)
    rules = pr.evaluate_course(pr.Policy.load(), pr.CourseFacts("A-", 100, None, True))
    p = FakeLLMProvider([submit(verdict="CHALLENGED", objections=[{"text": "x", "excerpt_ids": ["tgt-CS101-o1", "nope"]}])])
    rv = await run_review(p, REG, ctx(repo, role="reviewer"), m, rules)
    assert rv.status == "CHALLENGED" and rv.output.objections[0].excerpt_ids == ["tgt-CS101-o1"]
    assert "INVALID_CITATION_REMOVED:nope" in rv.flags
    payload = json.loads(p.requests[0].messages[1]["content"])
    assert "explanation" not in json.dumps(payload["matcher_recommendation"])  # narrative withheld
    none = await run_review(FakeLLMProvider([]), REG, c, await run_matching(FakeLLMProvider([submit(recommendation="NO_DEFENSIBLE_MATCH")]), REG, ctx(repo)), rules)
    assert none.status == "NOT_RUN"
