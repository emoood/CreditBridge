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


READ = [ToolCall("r", "get_target_course", {"course_id": "CS101"})]  # the matcher must read a course before claiming it


def submit(**kw):
    return [ToolCall("1", "submit_result", kw)]


def good_match(repo):
    s = repo.sources["COMP101"].excerpt_ids
    return {"recommendation": "POTENTIAL_MATCH", "target_course_id": "CS101",
            "supported_outcomes": [{"source_excerpt_id": s[0], "target_excerpt_id": "tgt-CS101-o1", "explanation": "same"}]}


async def test_happy_path_tool_round_then_submit(repo):
    p = FakeLLMProvider([READ, submit(**good_match(repo))])
    r = await run_matching(p, REG, ctx(repo))
    assert r.status == "POTENTIAL_MATCH" and r.agent.llm_calls == 2
    assert r.agent.tool_calls[0]["ok"] and "CS101" in r.case.targets_read["matcher"]


async def test_claiming_a_course_never_read_is_downgraded(repo):
    r = await run_matching(FakeLLMProvider([submit(**good_match(repo))]), REG, ctx(repo))
    assert r.status == "NEEDS_MORE_EVIDENCE" and "TARGET_NEVER_READ" in r.flags


async def test_fabricated_citation_removed_and_match_downgraded(repo):
    bad = good_match(repo)
    bad["supported_outcomes"][0]["target_excerpt_id"] = "tgt-CS101-o99"
    r = await run_matching(FakeLLMProvider([READ, submit(**bad)]), REG, ctx(repo))
    assert r.status == "NEEDS_MORE_EVIDENCE"
    assert "INVALID_CITATION_REMOVED:tgt-CS101-o99" in r.flags and "NO_VALID_CITATIONS" in r.flags


async def test_citation_from_wrong_course_rejected(repo):
    bad = good_match(repo)
    bad["supported_outcomes"][0]["source_excerpt_id"] = repo.sources["COMP220"].excerpt_ids[0]
    r = await run_matching(FakeLLMProvider([READ, submit(**bad)]), REG, ctx(repo))
    assert r.status == "NEEDS_MORE_EVIDENCE"


async def test_missing_syllabus_is_forced_to_needs_syllabus_with_request(repo):
    # even if the model claims a match, code overrides: no syllabus means no match
    p = FakeLLMProvider([READ, submit(**good_match(repo))])
    r = await run_matching(p, REG, ctx(repo, "ENGL101"))
    assert r.status == "NEEDS_SYLLABUS" and "NO_SOURCE_SYLLABUS" in r.flags
    assert repo.syllabus_requests[0]["course_id"] == "ENGL101"


async def test_unknown_subject_makes_no_llm_call(repo):
    p = FakeLLMProvider([])
    r = await run_matching(p, REG, ctx(repo, "NOPE999"))
    assert r.status == "NEEDS_MORE_EVIDENCE" and "SUBJECT_NOT_FOUND" in r.flags and p.requests == []


async def test_unknown_target_is_rejected(repo):
    m = good_match(repo)
    m["target_course_id"] = "ZZ999"
    r = await run_matching(FakeLLMProvider([submit(**m)]), REG, ctx(repo))
    assert r.status == "NEEDS_MORE_EVIDENCE" and "UNKNOWN_TARGET" in r.flags


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
    loop = [READ, *[[ToolCall(str(i), "get_policy_summary", {})] for i in range(2)]]
    p = FakeLLMProvider([*loop, submit(**good_match(repo))])
    r = await run_matching(p, REG, ctx(repo), max_rounds=3)
    assert r.status == "POTENTIAL_MATCH"
    assert [t["function"]["name"] for t in p.requests[-1].tools] == ["submit_result"]


async def test_tool_use_after_budget_exhausted_fails_safely(repo):
    tool = lambda i: [ToolCall(str(i), "get_policy_summary", {})]
    p = FakeLLMProvider([tool(1), tool(2), tool(3), "no usable answer"])  # still wants tools; synthesis also fails
    r = await run_matching(p, REG, ctx(repo), max_rounds=1)
    assert r.status == "NEEDS_HUMAN_REVIEW" and "TOOL_BUDGET_EXCEEDED" in r.flags and "FORCED_FINAL_NUDGE" in r.flags


async def test_nudge_after_budget_lets_the_model_submit(repo):
    tool = lambda i: [ToolCall(str(i), "get_policy_summary", {})]
    p = FakeLLMProvider([READ, tool(2), submit(recommendation="NO_DEFENSIBLE_MATCH")])
    r = await run_matching(p, REG, ctx(repo), max_rounds=1)
    assert r.status == "NO_DEFENSIBLE_MATCH" and "FORCED_FINAL_NUDGE" in r.flags


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
    m = await run_matching(FakeLLMProvider([READ, submit(**good_match(repo))]), REG, c)
    rules = pr.evaluate_course(pr.Policy.load(), pr.CourseFacts("A-", 100, None, True))
    p = FakeLLMProvider([submit(verdict="CHALLENGED", objections=[{"text": "x", "excerpt_ids": ["tgt-CS101-o1", "nope"]}])])
    rv = await run_review(p, REG, ctx(repo, role="reviewer"), m, rules)
    assert rv.status == "CHALLENGED" and rv.output.objections[0].excerpt_ids == ["tgt-CS101-o1"]
    assert "INVALID_CITATION_REMOVED:nope" in rv.flags
    payload = json.loads(p.requests[0].messages[1]["content"])
    assert "explanation" not in json.dumps(payload["matcher_recommendation"])  # narrative withheld
    none = await run_review(FakeLLMProvider([]), REG, c, await run_matching(FakeLLMProvider([submit(recommendation="NO_DEFENSIBLE_MATCH")]), REG, ctx(repo)), rules)
    assert none.status == "NOT_RUN"


async def test_general_elective_is_only_a_flag_and_skips_reviewer(repo):
    p = FakeLLMProvider([submit(recommendation="POTENTIAL_GENERAL_ELECTIVE", target_course_id="CS101")])
    m = await run_matching(p, REG, ctx(repo))
    assert m.status == "POTENTIAL_GENERAL_ELECTIVE" and m.output.target_course_id is None
    assert "GENERAL_ELECTIVE_FLAG_FOR_HUMAN" in m.flags
    rv = await run_review(FakeLLMProvider([]), REG, ctx(repo, role="reviewer"), m, [])
    assert rv.status == "NOT_RUN"


async def test_synthesis_fallback_rescues_a_model_that_will_not_submit(repo):
    tool = lambda i: [ToolCall(str(i), "get_policy_summary", {})]
    p = FakeLLMProvider([READ, tool(2), tool(3), submit(recommendation="NO_DEFENSIBLE_MATCH")])
    r = await run_matching(p, REG, ctx(repo), max_rounds=1)
    assert r.status == "NO_DEFENSIBLE_MATCH" and "SYNTHESIS_FALLBACK" in r.flags
    assert "Evidence you already gathered" in p.requests[-1].messages[1]["content"]


async def test_reviewer_is_advisory_challenge_keeps_the_pick_and_never_calls_the_matcher_again(repo):
    from backend.app.agents.orchestrator import run_case
    p = FakeLLMProvider([READ, submit(**good_match(repo)),
                         submit(verdict="CHALLENGED", objections=[{"text": "depth differs", "excerpt_ids": ["tgt-CS101-o1"]}],
                                notes=["minor wording"])])
    r = await run_case(p, repo, CatalogRetriever(list(repo.targets.values())), "COMP101")
    assert r.match.status == "POTENTIAL_MATCH" and r.match.output.target_course_id == "CS101"  # first pick stands
    assert r.review.status == "CHALLENGED" and r.review.output.notes == ["minor wording"]
    assert r.triage.category == "NEEDS_ATTENTION" and "objects" in r.triage.reasons[0]
    assert r.llm_calls == 3 and p.script == []  # matcher x2 (read, submit) + reviewer x1; no revision round
    assert [q.stage for q in p.requests].count("matching") == 2 and not any("depth differs" in str(q.messages) for q in p.requests if q.stage == "matching")


async def test_supported_review_with_strong_evidence_is_ready(repo):
    from backend.app.agents.orchestrator import run_case
    good = good_match(repo) | {"evidence_strength": "STRONG"}
    p = FakeLLMProvider([READ, submit(**good), submit(verdict="SUPPORTED_FOR_HUMAN_REVIEW")])
    r = await run_case(p, repo, CatalogRetriever(list(repo.targets.values())), "COMP101")
    assert r.review.status == "SUPPORTED_FOR_HUMAN_REVIEW" and r.triage.category in ("READY_TO_ACCEPT", "NEEDS_ATTENTION")


async def test_lookup_precedent_is_offered_only_once_real_precedents_exist(repo):
    from backend.app.agents.orchestrator import run_case
    from backend.app.agents.precedent import Precedent, PrecedentStore
    ret = CatalogRetriever(list(repo.targets.values()))

    def tool_names(p):
        return {t["function"]["name"] for t in p.requests[0].tools}

    empty = FakeLLMProvider([submit(recommendation="NO_DEFENSIBLE_MATCH")])
    await run_case(empty, repo, ret, "COMP101", PrecedentStore())
    assert "lookup_precedent" not in tool_names(empty) and "get_target_course" in tool_names(empty)
    seeded = FakeLLMProvider([submit(recommendation="NO_DEFENSIBLE_MATCH")])
    await run_case(seeded, repo, ret, "COMP101", PrecedentStore([Precedent("Intro", "demo", "CS101", "ACCEPTED")]))
    assert "lookup_precedent" in tool_names(seeded)


def test_general_elective_flag_sorts_with_needs_attention_and_never_ready():
    from backend.app.services.triage import triage
    ge = triage("POTENTIAL_GENERAL_ELECTIVE", None, "NOT_RUN", [], False, [])
    assert ge.category == "POTENTIAL_GE" and ge.priority == triage("POTENTIAL_MATCH", "PARTIAL", "CHALLENGED", [], False, []).priority
