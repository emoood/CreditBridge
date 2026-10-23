"""Score a model on the six fixture subjects through the full case workflow. Manual, uses the real model.
Usage: python benchmarks/model_eval.py [MODEL] [--provider gemini|ollama|lmstudio] [--think none|false|low|medium|high] [--runs N] [--tag NAME]
Scoring (hand-written rubric from fixtures/demo_v1/expected_results.json): 2 = intended outcome, 1 = acceptable
fallback, 0 = wrong, unsafe or failed. Max 12 per run."""
import argparse
import asyncio
from datetime import date
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.agents.orchestrator import CaseResult, run_case
from backend.app.config import get_settings
from backend.app.devlog import events
from backend.app.providers.gemini_provider import GeminiProvider
from backend.app.providers.guard import GuardedProvider
from backend.app.providers.ollama_provider import OllamaProvider
from backend.app.providers.openai_compat import OpenAICompatProvider
from backend.app.services.catalog_retriever import CatalogRetriever
from backend.app.services.demo_repo import DemoRepo

ORDER = ["COMP101", "STAT210", "COMP220", "SENG490", "INFS230", "ENGL101"]


def claimed(r: CaseResult) -> str | None:
    if r.match.output and r.match.output.target_course_id:
        return r.match.output.target_course_id
    return None


def score(code: str, r: CaseResult) -> tuple[int, str]:
    t, c, st = r.triage.category, claimed(r), r.match.status
    if st == "NEEDS_HUMAN_REVIEW":
        return 0, "agent failed to finish"
    if code == "COMP101":
        return (2, "ok") if c == "CS101" and t in ("READY_TO_ACCEPT", "NEEDS_ATTENTION") and st == "POTENTIAL_MATCH" else \
               (1, "claimed CS101 but withdrawn/flagged") if c == "CS101" else (0, f"expected CS101, got {c or st}")
    if code == "STAT210":
        return (2, "ok") if c == "STA201" and st == "POTENTIAL_MATCH" else (1, f"flag/other: {st}") if st == "POTENTIAL_GENERAL_ELECTIVE" or c == "STA201" else (0, f"expected STA201, got {c or st}")
    if code == "COMP220":
        return (2, "ok") if c == "CS310" and t == "BLOCKED_BY_RULE" else (1, "claimed CS310, not blocked (withdrawn)") if c == "CS310" else (0, f"expected CS310, got {c or st}")
    if code == "SENG490":
        return (2, "ok") if c == "CS450" and t == "BLOCKED_BY_RULE" else (1, "no match (acceptable, rule not shown)") if st == "NO_DEFENSIBLE_MATCH" else (0, f"got {c or st}/{t}")
    if code == "INFS230":
        if c == "CS340" and st == "POTENTIAL_MATCH":
            return 2, "found the better course"
        if c == "CS330" and st == "POTENTIAL_MATCH" and t == "READY_TO_ACCEPT":
            return 0, "FELL FOR THE TITLE TRAP (CS330 ready to accept)"
        if c == "CS330":
            return 1, "claimed CS330 (withdrawn/challenged)"
        return 0, f"expected CS340/CS330, got {c or st}"
    if code == "ENGL101":
        return (2, "no syllabus detected") if st == "NEEDS_MORE_EVIDENCE" and "NO_SYLLABUS" in r.match.flags and r.llm_calls == 0 else \
               (1, "no syllabus (called model)") if st == "NEEDS_MORE_EVIDENCE" and "NO_SYLLABUS" in r.match.flags else \
               (0, f"expected no syllabus/0 calls, got {st}/{r.llm_calls}")
    return 0, "?"


async def main(a):
    s = get_settings()
    model_name = a.model or (s.gemini_model if a.provider == "gemini" else "default")
    think = {"none": None, "false": False, "true": True}.get(a.think, a.think)
    if a.provider == "gemini":
        key = s.get_gemini_api_key()
        inner = GeminiProvider(key, model_name, 0.0, a.call_timeout + 10)
    elif a.provider == "lmstudio":
        inner = OpenAICompatProvider(s.lmstudio_base_url, model_name, "lmstudio", 0.0, a.call_timeout + 10)
    else:
        inner = OllamaProvider(s.ollama_mac_url, model_name, "eval", s.llm_context_tokens, 0.0, a.call_timeout + 10, think)
    prov = GuardedProvider(inner, a.call_timeout)
    repo = DemoRepo()
    ret = CatalogRetriever(list(repo.targets.values()))
    runs = []
    for n in range(a.runs):
        rows, t_run = [], time.time()
        for code in ORDER:
            events.clear()
            t0 = time.time()
            try:
                r = await run_case(prov, repo, ret, code, None, s.matcher_max_tool_rounds, s.reviewer_max_tool_rounds)
                pts, why = score(code, r)
                rows.append({"course": code, "points": pts, "why": why, "triage": r.triage.category, "match": r.match.status,
                             "claimed": claimed(r), "review": r.review.status if r.review else None,
                             "calls": r.llm_calls, "secs": round(time.time() - t0, 1),
                             "flags": [f for f in r.match.flags if f.startswith(("SYNTH", "FORCED", "TOOL_BUDGET", "INVALID", "TARGET_NEVER"))]})
            except Exception as e:  # noqa: BLE001
                rows.append({"course": code, "points": 0, "why": f"crash {type(e).__name__}: {str(e)[:100]}", "secs": round(time.time() - t0, 1)})
            print(f"  {code:8} {rows[-1]['points']} {rows[-1]['why']:48} {rows[-1].get('triage','')} {rows[-1]['secs']}s", flush=True)
        total = sum(x["points"] for x in rows)
        runs.append({"run": n + 1, "total": total, "secs": round(time.time() - t_run), "rows": rows})
        print(f"{model_name} run {n + 1}: {total}/12 in {runs[-1]['secs']}s", flush=True)
    out = Path(__file__).parent / "results" / f"model_eval_{date.today()}_{a.tag or model_name.replace(':', '-').replace('/', '-')}.json"
    out.write_text(json.dumps({"model": model_name, "think": a.think, "date": str(date.today()), "prompt": "matching_v4/reviewer_v4",
                               "runs": runs}, indent=1), encoding="utf-8")
    print("saved", out)

ap = argparse.ArgumentParser()
ap.add_argument("model", nargs="?", default="")
ap.add_argument("--think", default="false")
ap.add_argument("--runs", type=int, default=1)
ap.add_argument("--call-timeout", dest="call_timeout", type=float, default=120)
ap.add_argument("--provider", default="gemini", choices=["ollama", "lmstudio", "gemini"])
ap.add_argument("--tag", default="")
asyncio.run(main(ap.parse_args()))
