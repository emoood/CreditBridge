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

## 2026-10-22 LM Studio comparison (same six fixture subjects, qwen/qwen3.5-4b:2, MLX 4-bit, ctx 8192, 1 run each)
| Setup | Score | Time (6 subjects) | Notes |
|---|---|---|---|
| Ollama 4B, native tool calls | 8/12 | 363 s | no failures |
| LM Studio 4B, native tool calls | 7/12 | 665 s | no crashes, all 89 requests completed, no ERROR/WARN in the Developer log; slower per call |
| LM Studio 4B, JSON-action protocol (AGENT_PROTOCOL=json_action) | 5/12 | 165 s | ~17-21 s per subject, no failures, but shallower investigations (more "needs evidence"/elective flags) |
| Ollama 9B, native tool calls | 5/12 | 1097 s | 2 HTTP 500 tool-call parse errors, one 13-min hang |

LM Studio memory: LM Studio processes 1.1-3.8 GB RSS, free+inactive RAM never below 4.9 GB, swap flat (~4.5 GB, pre-existing).
LM Studio notes: structured output works (`response_format` json_schema) but the answer lands in `reasoning_content` when thinking is on; JSON-action schema (11 action variants) accepted. Always use the `:2` identifier.
Single runs on synthetic data: directional only. Hard per-call timeout (120 s, one retry) is now enforced on every provider.
Conclusion for the demo: Ollama 4B native is the most accurate of the tested setups; JSON-action is the fastest option if live speed matters.

## 2026-10-22 Background processing design (reading phase, officer gate, evaluation phase)
Audit first: every endpoint the UI called already existed with matching shapes; the gaps were the new behaviour (no confirm gate, no
resume, no restart safety, synchronous upload, evaluate allowed before verification, statuses the UI did not know).

- **State lives in a local PostgreSQL database `creditbridge`** (`APP_DATABASE_URL`), NOT in Supabase: the teammate owns that database and
  the curriculum schema is read-only for us. Tables: `applications`, `jobs`, `job_units`, `decisions`, `audit_events` (portable SQLAlchemy;
  unit tests run on in-memory SQLite). Curriculum data is read through `services/curriculum.py` against the existing database; no mock data,
  no tables created or altered there.
- **One in-process worker** (`workers/queue.py`): an asyncio queue and one consumer, started in the FastAPI lifespan. One job at a time. No Celery or Redis.
- **One model call at a time**, enforced by a lock inside `GuardedProvider` shared by the vision reader and both agents, so it holds even across jobs.
  The same wrapper enforces the hard per-call timeout (120 s, one retry, then `CallTimeout`). The model is kept warm with Ollama `keep_alive: 30m`.
- **Units**: one `job_units` row per stage and unit (page block in reading, subject in evaluation), unique per application, with status
  PENDING / RUNNING / DONE / FAILED_RETRYABLE / SKIPPED, attempts, method, error code, duration and result. Re-running a unit overwrites only its own row.
  Human decisions live in `decisions` and are never written by the pipeline; a decision whose suggestion was re-run is flagged `stale`.
- **Phase 1 (reading)**: render pages, split into blocks at whitespace, the vision model reads one block at a time through a `submit_block` tool
  (schema-constrained, null if unreadable), code cross-checks (`services/grade_checks.py`: mark to letter to points, credit and quality-point totals, GPA),
  one re-read at higher resolution for blocks that fail or are flagged, curriculum lookup with the string matcher; unmatched subjects are
  `NEEDS_SYLLABUS`, never guessed. Digital PDFs with a text layer use the deterministic parser instead. Ends at `EXTRACTION_REVIEW_REQUIRED` and stops.
- **Gate**: `POST /applications/{id}/confirm` (admissions role only) records officer, time and a field-level diff of corrections, then queues phase 2
  exactly once (row-locked; a replay returns the same job). `POST /evaluate` on an unconfirmed upload is refused (409).
- **Phase 2 (evaluation)**: per subject in sequence, MatchingAgent, rules, ReviewerAgent; each result is saved as it finishes; a failed subject
  becomes FAILED_RETRYABLE and the job continues; unrecognized subjects skip the model entirely. A triage worklist is built in code; the application
  becomes REVIEW_READY or EVALUATION_PARTIAL.
- **Restart**: on startup RUNNING jobs become INTERRUPTED (their running units FAILED_RETRYABLE), QUEUED jobs are queued again. `POST /jobs/{id}/resume`
  continues from the last DONE unit.
- **Privacy**: reading calls are logged with sizes only (no images, no transcribed text); audit details hold ids, counts and statuses; raw OCR text is not stored.
- Assumption: the school grade scale used for cross-checks is a synthetic US-style 4.0 scale (`policies/school_grade_scales.yaml`), not any school's real scale.

## 2026-10-22 Live end-to-end pass, fixtures/demo_v2 (synthetic image-only PDF), real model, real AUD 2026-27 catalog
Driven through the UI's own API calls by `scripts/e2e_live.py` on Ollama `qwen3.5:4b` (native tool calls). Final committed code; single run, not a benchmark.

| Stage | Real time |
|---|---|
| Upload response (job_id returned) | 0.06 s |
| Phase 1 reading, 5 blocks, vision model | 52.9 s (blocks 5.2-7.8 s each; one footer block needed a higher-resolution re-read; one footer block failed after 2 attempts and was flagged, harmlessly) |
| Cross-checks + curriculum lookup | under 1 s |
| Gate (confirm) | 0.02 s; replay returned the same job, nothing queued twice |
| Phase 2 evaluation, 7 subjects | 449.8 s total; per subject 37-166 s (the unrecognized subject: 0 s, no model call) |
| Resume of one failed subject | 129 s, only that unit redone, six DONE units untouched |
| Whole pass | 506 s |

Reading accuracy against the hand-derived answer key: 7/7 rows exactly right (subject, mark, letter, points, credits). Cross-check flags: none on the final run.
Earlier runs on the same file found and fixed: a dropped row with tall blocks (flagged by the cumulative totals), footer grading-scale legend read as 8 fake subjects, false term-total flags when a term spans two blocks.

Evaluation outcome (the weak spot): of 7 subjects, 1 READY_TO_ACCEPT (AP Statistics -> QUAN 201, reviewer supported), 2 NEEDS_ATTENTION (matcher first claimed the right course, e.g. AP Calculus AB -> MATH 210 STRONG,
then withdrew after a pedantic reviewer objection; the original claim and objections are shown to the officer), 2 NO_MATCH, 1 NEEDS_EVIDENCE, 1 unrecognized (Moral Education -> NEEDS_SYLLABUS, no model call).
One subject failed once with a model error and succeeded on resume. The 4B model is the limit: wrong-course picks (PSYC 210 for AP Psychology in one run), reviewer objections based on a 0.0 lexical-similarity hint
despite the prompt, matcher caving. Thin evidence also hurts (AP subjects have 4-6 curriculum objectives; AUD publishes one-paragraph descriptions, no outcomes).
Mitigations added: candidates pre-retrieved for the matcher with an introductory-course/title ranking boost for school subjects, "be decisive" and "school vs university credit counts are not comparable" prompt rules (matching_v4/reviewer_v4).
Not yet tried: a stronger model for the matcher/reviewer (LM Studio or a larger Ollama model), a recorded run for the demo.

## 2026-10-22 Field-test phase: design changes (prompts untouched: matching_v4, reviewer_v4)
1. **Reviewer is advisory only.** The revision round is gone: the reviewer's objections (with excerpt ids) and a new `notes` field (minor remarks) are shown next to the matcher's pick, and
   nothing is fed back to the matcher. The matcher's first pick always stands. Triage: POTENTIAL_MATCH + SUPPORTED_FOR_HUMAN_REVIEW (and STRONG evidence, rules pass) = READY_TO_ACCEPT;
   POTENTIAL_MATCH + CHALLENGED = NEEDS_ATTENTION with the objection visible. The CONTESTED category, `original_claim`, `revised` and `response_to_objections` were removed.
   Why: in the demo_v2 run the 4B matcher withdrew correct first picks after pedantic objections; with an advisory reviewer the officer sees both and decides.
2. **Speed without prompt changes.** Matcher max tool rounds 10 -> 6. `lookup_precedent` is offered to the agents only when real officer decisions exist (an empty store cost a model turn for nothing).
   Caveat: matching_v4 still tells the matcher to "check lookup_precedent"; while the tool is unregistered a model that tries it gets "not available" (one wasted turn at worst). Fix belongs in the next prompt version.
   Before (demo_v2, final run, same code except these two changes): 25 / 10 / 27 / 12 / 14 / 8 model calls and 127 / 46 / 166 / 53 / 69 / 38 s for the six subjects the model ran (avg 16 calls, 83 s).
3. **Title-only suggestions for subjects with no syllabus.** Status stays NEEDS_SYLLABUS and the syllabus request is still drafted; only that subject is blocked. The matcher gets a per-subject instruction in the user
   message (no prompt file edited) and a new output field `title_only_suggestions` (up to 3 existing catalog courses, a one-line reason each). Code keeps only courses that exist in the catalog, drops duplicates,
   forces `target_course_id=None`, no citations, no `supported_outcomes`, and labels each `TITLE_ONLY_UNVERIFIED`. The referee still turns any POTENTIAL_MATCH without a syllabus into NEEDS_SYLLABUS.
   UI: a separate dashed amber/grey box "AI judgment from title only, no syllabus on file", no accept button. Cost: an unrecognized subject now costs 1-3 model calls (max 2 tool rounds) instead of 0.
4. **UI**: POTENTIAL_GENERAL_ELECTIVE is amber, labelled "AI: general elective, officer decides", and sorts with NEEDS_ATTENTION (priority 1). A page element that could not be read is described as a skipped non-course element, with a
   reminder to compare the table with the scan.
6. **Recorded run / replay.** `data/recordings/<name>/` (gitignored): recording.json (rows, reading summary, every subject's full result with citations and excerpt text, timings, model, date, traces) + the scan.
   Replay loads it at the officer gate (the gate still applies) and fills each subject from the recording with `is_replay: true` and `replay.label = "Replay of recorded run, <date>, <model>"`; a subject can be named `live`, then
   the model runs it now and it carries no label. A replayed result is used only if the row's title and linked curriculum subject still match the recording; an officer-edited row falls back to live.

## 2026-10-22 School-specific subjects are not mapped (product owner decision)
Moral Education, Physical Education, Islamic and Arabic A (and UAE Social Studies, my addition) exist only in the school system; university requirements differ, so they cannot be mapped.
Transfer credit covers beginner-level major courses and general electives, and electives need an officer check. These subjects now skip the model entirely and come back as SCHOOL_SPECIFIC
(flag `SCHOOL_SPECIFIC_NOT_MAPPED`, grey, bottom of the list): not mapped, and NOT flagged as a general elective either. A general elective is a different thing:
a non-school-specific subject whose university equivalent would be higher level (e.g. a school statistics course, since any probability/statistics course here is upper level) can be flagged POTENTIAL_GENERAL_ELECTIVE by the matcher. The list is `policies/school_specific_subjects.yaml` (substring match on the transcript title; edit freely).
Effect on demo1: about 9 of 31 rows no longer cost model calls (3 Islamic A took 24-33 s each before).

## 2026-10-23 Display config and the first transfer rule (product owner)
- `policies/institution.yaml` controls what the officer UI shows (another institution edits this file): mark, school credits, AUD credits (shows `transfer_credits`, 3 for AUD) and grade year (the row's `term` field, hidden for now) are all off.
  They stay in the data and the CSV export includes AUD credits only when enabled. Served by `GET /api/v1/config/institution`.
- The extraction table shows course, grade and the curriculum course CODE (from the curriculum database, e.g. AP-CALC-AB) with the subject name only when it differs from the transcript title.
- New enforced rule `AUD_NON_TRANSFERABLE_AREA` (policy `non_transferable_departments: [ENGL]`): English courses never take transfer credit. Such courses are never offered as search candidates and any pick in them is BLOCKED_BY_RULE.
- Not built yet, waiting for the logic: transfer covers only beginner-level major-general courses (Calculus I only, physics or biology for engineering students) plus general electives, with a per-course allow-list.
  Today the retriever only prefers 100-200 level courses; nothing stops a pick outside the future allow-list.

## 2026-10-23 First working path: code-first evaluation with a Case agent
Replaces "the model evaluates every subject". Flow after the officer confirms the extraction (`case_agent: true`, the default; replays and `CASE_AGENT=false` keep the older path):
1. **Relevance gate** (code, `policies/curricula.yaml`): only externally examined qualifications accepted for the curriculum (AP, A/AS Level, Edexcel Level 3, IB; set per curriculum in University Setup, a file for now) are evaluated.
   The rest are "Not evaluated" with a reason and a one-click include (`POST /rows/{id}/include`, persisted, works that row only). School-specific subjects stay not evaluated and cannot be included.
2. **Batch mapping lookup** (code): one query against the local dummy `course_mappings` table (`policies/dummy_mappings.yaml`: course, AUD equivalent, credits, optional condition, version, status) keyed by the curriculum database subject code.
   Conditions: `min_grade` (from the transcript), `min_exam_score` and `exam_variant` (need evidence). Policy rules run on the mapped target. Mapped = active mapping, condition met, no rule failure.
   No mapping, unmet or unprovable condition, stale mapping, or a rule failure = **Needs you**.
3. **Row state** (`row_states`): status, kind, reason, mapping snapshot, condition verdict, finding, drafted requests and collected facts per row, so work pauses and resumes for just that row.
4. **Usage ledger** (`usage_ledger`: every model call, seconds, tokens, cost; local models cost $0) and **trace** (`trace_events`: every Case-agent tool action and the code steps), served at `GET /applications/{id}/activity` and shown in the activity drawer.
5. **Case agent** (`agents/case_agent.py`, prompt `case_v1`): works only "Needs you" rows. Tools: `read_mapping`, `read_source_excerpts`, `read_target_excerpts`, `check_policy`, `propose_candidates`, `draft_request`, `set_row_state`.
   Missing evidence -> says exactly what, drafts a request (nothing is sent), sets "Waiting on document" (refused without an open request). Unmapped -> `propose_candidates` runs the existing matcher + advisory reviewer
   (refused for rows that have a mapping), then states gaps and a short review package. It can never set "Mapped" (only the code lookup can) and has no approval path. Output is a finding.
   Resume: `POST /rows/{id}/evidence` (facts such as `{"exam_score": 5}` and/or a file) marks the open requests received and queues a one-row job: the code re-checks the condition first (often settling the row with no model call), else the agent runs on that row only.
Not run against a real model yet (by request); tests use fake providers. The mapping table is a placeholder: its AUD codes and conditions are made up for the demo.
