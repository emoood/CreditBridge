"""The Case agent: the only model-using part of the evaluation path. It works rows that code left as "Needs you", decides what each
needs (missing evidence, an unmet condition, no mapping), drafts requests, and writes a finding for the officer. It never approves.
The matcher and reviewer live inside it as the `propose_candidates` tool, used only for unmapped courses (the reviewer stays advisory)."""
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from ..config import get_settings
from ..db.session import app_session
from ..domain.schemas import AgentOutput
from ..providers.base import LLMProvider
from ..services import mapping as mp
from .loop import run_bounded_agent
from .orchestrator import CaseResult
from .tools.base import ToolRegistry  # noqa: F401  (re-exported type for callers)
from .tools.case_tools import CASE_TOOLS, CaseContext, TracedRegistry

PROMPT_VERSION = "case_v3"
PROMPT = (Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.txt").read_text()


class CaseCandidate(AgentOutput):
    aud_code: str
    why: str = ""


class CaseFinding(AgentOutput):
    headline: str
    needs: Literal["NEEDS_YOU", "WAITING_ON_DOCUMENT"] = "NEEDS_YOU"
    missing_evidence: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    candidates: list[CaseCandidate] = Field(default_factory=list)
    review_package: list[str] = Field(default_factory=list)
    questions_for_chair: list[str] = Field(default_factory=list)


async def run_case_agent(provider: LLMProvider, repo, retriever, precedent, app_id: str, row: dict[str, Any], source_id: str, mode: str = "work") -> dict[str, Any]:
    """Work one row. `provider` should be the metered provider so every call lands in the usage ledger."""
    ctx = CaseContext(repo, retriever, "case", source_id, precedent=precedent, app_id=app_id, row_id=row["id"], provider=provider)
    with app_session() as db:
        st = mp.get_state(db, app_id, row["id"])
        brief = {"row": {"title": row.get("title"), "grade": row.get("grade")}, "subject": row.get("subject"), "kind": st.kind if st else "", "reason": st.reason if st else "",
                 "open_requests": [r for r in (st.requests if st else []) if r["status"] in ("DRAFT", "SENT")],
                 "mode": mode, "officer_notes": [n["text"] for n in ((st.facts or {}).get("_notes", []) if st else [])], "previous_finding": (st.finding if st else None)}
    outcome = await run_bounded_agent(provider, TracedRegistry(CASE_TOOLS), ctx, system=PROMPT, user=json.dumps(brief, ensure_ascii=False),
                                      final_model=CaseFinding, max_tool_rounds=get_settings().case_max_tool_rounds, stage="case",
                                      max_seconds=get_settings().case_max_seconds)
    flags = list(outcome.flags)
    finding: dict[str, Any]
    if outcome.status == "OK" and outcome.output is not None:
        out: CaseFinding = outcome.output
        cands = [c.model_dump() for c in out.candidates if c.aud_code in repo.targets and c.aud_code.split()[0].upper() not in retriever.excluded]
        finding = {**out.model_dump(), "candidates": cands}
    else:
        flags.append(outcome.error or "AGENT_FAILED")
        finding = {"headline": "The Case agent could not finish this row. It stays 'Needs you' for the officer.", "needs": "NEEDS_YOU", "missing_evidence": [], "gaps": [],
                   "candidates": [], "review_package": []}
    finding.update({"by": "case_agent", "prompt_version": PROMPT_VERSION, "flags": flags})
    with app_session() as db:
        st = mp.save_state(db, app_id, row["id"], finding=finding)
        status = st.status
    return {"finding": finding, "status": status, "proposal": ctx.proposal, "llm_calls": outcome.llm_calls, "tool_calls": outcome.tool_calls}


def proposal_of(res: dict[str, Any]) -> CaseResult | None:
    return res.get("proposal")


async def run_case_review(provider: LLMProvider, repo, retriever, precedent, app_id: str) -> dict[str, Any]:
    """The whole-application pass: the Case agent reviews ALL rows (mapped ones too), raises doubts that code corroborates, judges borderline electives
    against the free-elective allowance and writes a short summary for the officer. It never changes a row's status."""
    ctx = CaseContext(repo, retriever, "case", "", precedent=precedent, app_id=app_id, row_id="", provider=provider)
    s = get_settings()
    outcome = await run_bounded_agent(provider, TracedRegistry(CASE_TOOLS), ctx, system=PROMPT, user=json.dumps({"mode": "review_all"}), final_model=CaseFinding,
                                      max_tool_rounds=s.case_review_max_tool_rounds, stage="case_review", max_seconds=s.case_review_max_seconds)
    ok = outcome.status == "OK" and outcome.output is not None
    return {"summary": outcome.output.headline if ok else "", "ok": ok, "by": "case_agent", "prompt_version": PROMPT_VERSION, "flags": list(outcome.flags) + ([] if ok else [outcome.error or "AGENT_FAILED"]),
            "llm_calls": outcome.llm_calls, "tool_calls": len(outcome.tool_calls)}
