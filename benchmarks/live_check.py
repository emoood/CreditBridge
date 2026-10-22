"""Manual live run against the real local model. NOT part of pytest. Prints results; does not assert."""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.agents.matching_agent import run_matching
from backend.app.agents.reviewer_agent import run_review
from backend.app.agents.tools.base import ToolContext, ToolRegistry
from backend.app.agents.tools.evidence_tools import ALL_TOOLS
from backend.app.config import get_settings
from backend.app.devlog import events
from backend.app.providers.router import build_provider
from backend.app.services import policy_rules as pr
from backend.app.services.catalog_retriever import CatalogRetriever
from backend.app.services.demo_repo import FIXTURES, DemoRepo


async def main(codes):
    repo = DemoRepo()
    ret = CatalogRetriever(list(repo.targets.values()))
    reg, prov, pol = ToolRegistry(ALL_TOOLS), build_provider(get_settings()), pr.Policy.load()
    exp = {s["source_code"]: s for s in json.loads((FIXTURES / "expected_results.json").read_text())["scenarios"]}
    rows = []
    for code in codes:
        t0 = time.time()
        m = await run_matching(prov, reg, ToolContext(repo, ret, "matcher", code))
        tgt = m.output.target_course_id if m.output else None
        rules, rv = [], None
        if tgt:
            t = repo.targets[tgt]
            rules = pr.evaluate_course(pol, pr.CourseFacts(repo.sources[code].grade, t["level"], t["restricted_type"], True))
            rv = await run_review(prov, reg, ToolContext(repo, ret, "reviewer", code), m, rules)
        rows.append({"course": code, "scenario": exp[code]["scenario"], "intended": exp[code]["intended_target"],
                     "matcher": m.status, "target": tgt, "m_calls": m.agent.llm_calls if m.agent else 0,
                     "m_tools": [t["name"] for t in m.agent.tool_calls] if m.agent else [], "m_flags": m.flags,
                     "reviewer": rv.status if rv else None, "r_flags": rv.flags if rv else [],
                     "objections": [o.text for o in rv.output.objections] if rv and rv.output else [],
                     "hard_fail": [r.rule_id for r in rules if r.status == "FAIL"], "secs": round(time.time() - t0, 1)})
        print(json.dumps(rows[-1], ensure_ascii=False, indent=1), flush=True)
    calls = [e for e in events.recent(2000) if e["kind"] == "llm_call"]
    print("LLM calls:", len(calls), "avg s:", round(sum(c["duration_s"] for c in calls) / max(1, len(calls)), 2))

asyncio.run(main(sys.argv[1:] or ["COMP101", "STAT210", "COMP220", "SENG490", "INFS230"]))
