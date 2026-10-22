# agents/

Everything about the two AI agents lives here, so it can be read and studied as a unit.

| Path | What it is |
|---|---|
| `loop.py` | Bounded tool-calling loop. Finishes via a `submit_result` tool (Ollama MLX has no `format`). Pydantic-validates the result, one repair attempt, forced final turn after the tool budget. |
| `matching_agent.py` | MatchingAgent: deterministic pre-checks (no syllabus => `NEEDS_SYLLABUS`, no candidates => `NO_DEFENSIBLE_MATCH`), runs the loop, then removes invalid citations and downgrades unsupported matches. |
| `reviewer_agent.py` | ReviewerAgent: gets primary evidence + rule results, NOT the matcher's explanations. Only runs on a `POTENTIAL_MATCH`. |
| `prompts/` | Versioned prompts (`matching_v1.txt`, `reviewer_v1.txt`). |
| `tools/base.py` | Tool registry: pydantic arg validation, per-role visibility, resource scope, result truncation, event logging. |
| `tools/evidence_tools.py` | The 7 read-only tools and which role may call each. |

Not here by design: output schemas (`domain/schemas.py`, contain no approval field), the rules engine
(`services/policy_rules.py`, no LLM), providers (`providers/`). The agents never approve credit.

Tests: `backend/tests/unit/test_agents.py` (fake provider). Live check: `benchmarks/live_check.py`.
