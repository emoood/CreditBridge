"""Manual live run against the real local model through the full case workflow. NOT part of pytest."""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.agents.orchestrator import run_case
from backend.app.config import get_settings
from backend.app.devlog import events
from backend.app.providers.router import build_provider
from backend.app.services.catalog_retriever import CatalogRetriever
from backend.app.services.demo_repo import DemoRepo


async def main(codes):
    s = get_settings()
    repo = DemoRepo()
    ret = CatalogRetriever(list(repo.targets.values()))
    prov = build_provider(s)
    for code in codes:
        t0 = time.time()
        r = await run_case(prov, repo, ret, code, matcher_rounds=s.matcher_max_tool_rounds, reviewer_rounds=s.reviewer_max_tool_rounds)
        out = r.summary() | {"secs": round(time.time() - t0, 1), "queries": r.case.queries,
                              "notes": r.case.notes, "objections": [o.text for o in r.review.output.objections] if r.review and r.review.output else []}
        print(json.dumps(out, ensure_ascii=False, indent=1), flush=True)
    calls = [e for e in events.recent(5000) if e["kind"] == "llm_call"]
    print("LLM calls:", len(calls), "avg s:", round(sum(c["duration_s"] for c in calls) / max(1, len(calls)), 2))

asyncio.run(main(sys.argv[1:] or ["COMP101", "STAT210", "COMP220", "SENG490", "INFS230", "ENGL101"]))
