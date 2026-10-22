# Decisions log

## 2026-10-22 Step 0 spikes (Mac, Ollama 0.40.8, qwen3.5:4b, 8192 ctx, temp 0)

Model runs on Ollama's **MLX runner, 100% GPU**, 4.2 GB resident.

### LLM findings
| Check | Result |
|---|---|
| Tool call round (`think:false`) | Works, parseable `tool_calls`, ~2-4 s |
| Tool call with `think:true` | Works, emits a `thinking` field (~400 chars), more tokens |
| `format` (JSON schema or `"json"`) | **Fails: HTTP 501 "structured output is unavailable"** on the MLX runner |
| Tools + `format` | Not usable (same reason) |
| Final answer as a `submit_result` tool call | Works, arguments parse as the schema, ~2 s |
| Plain "return ONLY JSON" prompt | Works on a simple case, no guarantee |

**Decision:** Do not rely on `format`. Agents finish by calling a `submit_result` tool whose parameters are the output schema. The result is always validated with Pydantic, with one repair attempt, then `NEEDS_REVIEW` (plan Sections 4.4, 6.3). Use `think:false` by default.

**Quality warning:** with a bare prompt and no real evidence, the 4B model said `POTENTIAL_MATCH` for Intro Programming vs a Capstone Project, and `NO_DEFENSIBLE_MATCH` where `NEEDS_MORE_EVIDENCE` was right. So the guardrails carry the safety, not the model: the restricted-type rule is deterministic, missing evidence is detected in code before the model is asked, and citations are validated. The ReviewerAgent and prompts need testing against the planted cases in the fixtures.

### Arabic OCR spike (Tesseract 5.5.3, `ara` data via tesseract-lang)
Synthetic bilingual page (Latin codes, Arabic titles, grades), mild skew and blur, `--psm 6`.
- Course codes and credits: read correctly with both `ara+eng` and `eng`.
- Arabic titles: `ara+eng` mostly correct (one wrong letter in 6 rows, e.g. "الأكاريمية"); `eng` alone gives garbage, as expected.
- Grades under `ara+eng`: 3 of 6 misread (`B+` as `8+`, `C` as `6`, `B` as `8`). `eng` alone read all 6 grades correctly.

**Decision:** two-pass OCR. `eng` pass for code, credits and grade; `ara+eng` pass for the title column. Cross-check against grade/credit validity patterns and flag anything that fails or disagrees for human verification. Viable, so no fallback needed yet. One synthetic page only, so no accuracy claims.

## Open
- Final model choice (4B vs 9B) after timing runs.
- Supabase connection string to be supplied; local PostgreSQL 18 is installed for now.

## 2026-10-22 First live agent runs (qwen3.5:4b, Mac, 5 courses with syllabi; `benchmarks/live_check.py`)
Single runs, not a benchmark. n=5 courses, one run each.

**Run 1 (reviewer saw all rule flags and the "demo catalog" label):**
- Matcher: POTENTIAL_MATCH for all 5. Intended target chosen for 4/5; for the title trap (INFS230) it chose CS340 (the better match), not CS330.
- Reviewer: noisy. INSUFFICIENT_EVIDENCE for 3/5 with irrelevant objections (demo catalog, static NEEDS_REVIEW flags), one invented "source is level 200" claim, one made-up excerpt id in free text.
- 21 LLM calls, 8.9 s average, 27-42 s per course.

**Change:** reviewer now sees only enforced rule results and a note that staff checklist items are not objections; "demo catalog" label removed from tool data; prompt told to quote ids exactly.

**Run 2:** 22 calls, 7.1 s average, 20-40 s per course.
- COMP101: SUPPORTED. STAT210: CHALLENGED, correctly noted the missing regression outcome. COMP220 and SENG490: CHALLENGED, objections quoted the failed rule (grade, restricted capstone).
- INFS230 (title trap): matcher chose CS340; reviewer returned NEEDS_HUMAN_REVIEW (agent did not produce a valid result). Needs investigation.
- Raw output: `benchmarks/results/live_check_2026-10-22_run2_qwen3.5-4b.json`.

**Implications:** (1) the scenario 5 trap is defused by retrieval + matcher on this run, so the demo should force/show the CS330 candidate to exercise the reviewer's challenge; (2) free-text ids in objections are not validated, only the structured `excerpt_ids`; (3) 6 courses is roughly 3-4 minutes per full pass, inside the 10-minute live budget.

## 2026-10-22 Model comparison, same six fixture subjects, prompts matching_v3/reviewer_v3 (`benchmarks/model_eval.py`, 1 run each)
Rubric: 2 = intended outcome, 1 = acceptable fallback, 0 = wrong or failed; max 12. Single runs, directional only.

| Model (Ollama, MLX runner) | Score | Time for 6 subjects | Notes |
|---|---|---|---|
| qwen3.5:4b | 8/12 | 363 s | All cases finished. Clear match reached "ready to accept". |
| qwen3.5:9b | 5/12 | 1097 s | 2 crashes (Ollama: "qwen3.5 tool call parsing failed: XML syntax error", HTTP 500, one hung ~13 min). 14 GB resident. Slower per call. |

Conclusion so far: on Ollama's MLX runner the 9B is slower and less reliable than the 4B, so the 4B stays the default.
Next: LM Studio provider is written (`providers/openai_compat.py`) but untested live; try the same Qwen weights there and another tool-capable model.
Raw results: `benchmarks/results/model_eval_2026-10-22_*.json`.
