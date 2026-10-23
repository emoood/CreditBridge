# Overnight report (updated after every part)

Rules followed: work on `main`, push after every part, no force-push, tests and lint green before each push, synthetic data only, local model only.
Rollback point: tag `pre-overnight` = `3801f36` (the last commit before this run).

## Status
Tag `pre-overnight` = `3801f36` (everything before this run; `git reset --hard pre-overnight` on a branch would roll back).
| Part | State | Commit |
|---|---|---|
| Pre: push unpushed commits, tag | done | `3801f36` |
| 0. Delete the degraded B recording and files | done | `4711876` |
| 1. Local model speed set-up | done | `5b8693f` |
| 2. Transcript A2 (A untouched) | done | `0c6072e` |
| 3. Planted misread, failed-block re-read, term-total questions | done | `898902a` |
| 4. Real runs: A2, B, C | A2 recorded, C recorded, **B failed and was not recorded** | `f34b7c6` (driver, richer recordings), `ee8a679` (results) |
| 5. Replay mode | done | `941cfa3` |
| 6. README runbook | done | `02c3705` |
| 7. Small tests | added in each part (103 tests pass, lint clean) | |
| Final: this report, full test and lint run | done | last commit |

## Part 0
Deleted `data/recordings/demo_b_intake_degraded`, `fixtures/demo_v3/transcript_b_intake_degraded.*`, the generator's degrade branch and the app record that produced it. A, B and C fixture files were restored byte-for-byte (the generator is not to be rerun for A).

## Part 1: speed set-up
- Thinking off: `LLM_THINK=false` (default), passed to the Ollama provider explicitly. Warm-up check: 0 thinking characters, reply "ready", 1 output token.
- Case agent capped at 3 tool rounds (`CASE_MAX_TOOL_ROUNDS`, was 6).
- Keep-alive raised from 30 minutes to 24 hours. The API preloads the model and sends one warm-up call on start (`WARMUP_ON_START`); `python scripts/warmup.py` does the same by hand.
- Measured on this Mac: model already resident, warm call 0.3 s.
- Tests: defaults, provider wiring, warm-up never raises when the server is down (`test_speed_setup.py`).

## Part 2: Transcript A2
`fixtures/demo_v3/transcript_a2_batch.pdf`: same 11 rows and grades as Transcript A, different invented student (Rania Haddad, SYN-1004), text layer, printed grading scale. Made by `fixtures/generate_demo_v3_a2.py`; the main generator now never rewrites A, B or C unless `--force`.
Transcript A is byte-identical to before (sha256 `7e57a9f0f260...`) and has **no application record** (checked in the local database by its hash: 0 applications).
Test: A2 has the same courses as A, a different student, and parses to the expected rows.

## Part 3: Intake recheck made demonstrable
- **Planted misread** (`PLANTED FOR DEMO`): upload checkbox "Demo: plant a misread (Transcript B)" (only while `DEMO_MODE` is on). It replaces the AP Chemistry mark (read as 85) with 65 in BOTH passes of the normal reader, before the extraction checks run, so the grading-scale check fails and the normal re-read cannot fix it. The Intake agent's own focused re-reads are never planted. Logged to the activity trace as `planted_for_demo`; the extraction screen shows a purple PLANTED FOR DEMO banner with the original and replaced values.
- **Failed blocks:** a block the model could not read at all (invalid output) is now a failing block. The Intake agent re-reads it (400 dpi crop); the new reading is accepted only if it yields course rows and every row passes its checks, otherwise the block stays failed.
- **Term totals:** a failed credit, grade-point or GPA total now attaches a specific question to every row of that term, naming the printed and the computed number ("The printed term credit total is 7.5 but the rows add up to 2. Check the credits of 'AP Biology' against the scan, or whether a row is missing."). Prompt `intake_v2`.
- Tests: planted misread changes only that mark and trips the check; failed-block acceptance rule; totals become per-row questions and an unreadable block is a failure.
- Not tested with a real model yet (Part 4).

## Part 4: real runs on the local model (qwen3.5:4b via Ollama, thinking off, warmed, one run per transcript, no retries, nothing tuned)
Driver: `scripts/overnight_runs.py` (raw results in `data/overnight/*.json`, gitignored). Recordings now also keep row states, the agent trace, tool actions, the usage ledger, officer actions, timings and the planted flags.

### A2 (Rania Haddad, SYN-1004; text layer): clean, recorded as `demo_a2`
| Measure | Result |
|---|---|
| Time to table (upload to extraction table) | 6.1 s (digital parser, no model) |
| Time to first result | 16.7 s after Confirm (22.8 s from upload). The first unit was the Calculus row, which goes to the agent; mapped rows were queued behind it. A cheap ordering change (mapped rows first) would make the first result instant; not done (outside this list). |
| Total | evaluation 26.9 s; whole scripted run 33.5 s |
| Model calls | 7, all stage `case` (23.4 s of model time, **3.34 s per call**, 13,559 prompt / 776 output tokens) |
| Tool calls | 5, all accepted, none refused. Calculus AB: `read_mapping`, `draft_request`, `set_row_state`(Waiting on document), submit. Computer Science A: `read_mapping`, `set_row_state`(Conditional), submit. |
| Case agent on AP Calculus AB | Correct: found the missing AP score (mapping needs at least 4), drafted exactly one request ("AP exam score report"), set Waiting on document. Then exam score 5 submitted: resumed that row only, **0 model calls** (code re-checked the condition) and it became Mapped. |
| Everything else | 9 rows Mapped by code with no model call, Moral Education Not evaluated (school-specific); "Confirm mapped" confirmed 9. Computer Science A stayed Conditional (stale mapping to ENGG 140), finding worded correctly. |
| Model got wrong | Nothing material. |

### B (Daniel Ferreira, SYN-1002; image only, planted misread ON): FAILED, not recorded
- Reading took 88.5 s. The planted misread **never fired** (`applied: []`): the vision model returned **zero rows** for block p1b2 (the block holding six of the eight courses) on both passes, with a *valid* empty answer, so nothing was reported as failed. The first block's header line was read as five junk "rows" (Subject, Mark, Grade, Points, Credits) and the legend as two rows.
- Only AP Statistics and AP Human Geography came through. The term-total checks failed (credits computed 1.5 vs printed 7.5). The Intake agent re-read p1b3 three times (the block holding the totals, not the empty p1b2), accepted nothing, and flagged the two rows with specific total questions (4 accepted `flag_cell` calls); it also made **2 refused `flag_cell` calls** (a field called "GPA" that is not a cell).
- Model calls: 10 (7 intake, 3 vision reads), 5.06 s per call. Tool misuse (2 refusals) plus no corrected value, so per your rule: not recorded.
- Findings: (1) a block that returns an empty reading is invisible to the checks (not "invalid output"), so the "failed block" path never triggers; (2) the check points at the block that holds the printed totals, not the block that lost the rows; (3) this read was worse than the earlier B reads on the same file (all 8 rows), so reading is not stable run to run at temperature 0. Not fixed (no retries, no tuning, no new features).

### C (Sofia Lindqvist, SYN-1003; text layer + planted copy error): clean, recorded as `demo_c_copy_error`
| Measure | Result |
|---|---|
| Time to table | 2.0 s; evaluation 0.1 s (all rows mapped by code); first result 2.2 s |
| Planted copy error | AP Statistics QUAN 201 changed to ACCG 201 in the output stage ("planted for demo" in the trace). The normal `matches_mapping` check failed on exactly that row; the sheet had passed before the plant. |
| Auditor | Ran only after the check failed. 4 model calls, 13.1 s (3.28 s per call), 7,370 prompt / 605 output tokens. Tools: `read_transcript_row`, `read_mapping_record`, `read_policy_result`, `propose_correction`, `rerun_checks`, all accepted. It proposed ACCG 201 back to QUAN 201 (3 credits), flagged as touching a confirmed row, and the corrected copy passes. Nothing was applied automatically. |
| Model got wrong | Its explanation says "credits from 1 to 3" but the sheet already said 3 credits (it quoted the school credits); the proposal itself is right. |
| Total | whole scripted run 16.4 s |

## Part 5: replay mode
- A recording of a Case-agent run now restores, **without calling any model**: every row's state and finding, the drafted requests, the officer's actions (e.g. the 9 confirmations), the agent trace, the usage ledger, the planted flags and the Auditor report. Starting a replay (Worklist, "Replay a recorded run") creates an application at the officer gate with the recorded student name and ID and the scan; pressing Confirm loads the recorded results.
- **Live/replay per application:** replaying is one choice, uploading is the other. The batch (A2, C) comes from recordings; Transcript A is uploaded live. Inside a replay, rows chosen as "run live" go through the normal lookup and Case agent now, and are not marked as recorded.
- **Honest labelling:** the label "Replay of recorded run, <date>, <model>" shows once, as a banner at the top of the student popup (not on every row); it says no model was called (or which rows ran live). The activity drawer tags each trace entry "recorded", reports "N from the recording, M live" model calls, and recorded ledger rows carry the provider prefix `recorded:`.
- Real check on the two real recordings (no model): `demo_a2` loads and evaluates in 0.15 s (statuses Conditional/Mapped/Not evaluated, 9 actions restored, sheet passes, 7 recorded calls, 0 live); `demo_c_copy_error` in 0.06 s (sheet fails with the planted row, the Auditor report is present, 4 recorded calls, 0 live). Looked at in Chrome (synthetic only).
- Older recordings (field-test format) still replay through the earlier path.
- Test: record a fake-model run, replay it with zero model calls and identical statuses and actions, trace marked recorded; replay with one row live: the model is called for that row only and it is not marked as a replay.

## Part 6: README runbook
Added "Demo runbook" to the README: start command, preflight, warm-up, a before/after table (A live; C and A2 replay; B not available), what to say, and the fallback (switch to `demo_a2` and say it is a recording). The runbook says plainly that the Intake recheck has no recording.

## Part 7: tests
Small fake-provider tests were added with each part: speed defaults and warm-up failure (2), A2 vs A (1), planted misread, failed-block acceptance, per-row total questions (3), replay without a model with recorded marking and one live row (1). Full suite: 103 passed, lint clean, before the final push.

## Summary of every real-model result (qwen3.5:4b, Ollama, thinking off)
| Run | Outcome | Model calls | s per call | Time |
|---|---|---|---|---|
| A2 | clean, recorded `demo_a2` | 7 | 3.34 | table 6.1 s, first result 16.7 s after Confirm, evaluation 26.9 s, scripted run 33.5 s |
| B (planted misread on) | failed, not recorded | 10 | 5.06 | table 88.5 s |
| C | clean, recorded `demo_c_copy_error` | 4 (Auditor) | 3.27 | table 2.0 s, Auditor 14.1 s, scripted run 16.4 s |

## What failed or was skipped, and why
- **B (Intake recheck demo): failed.** The model returned a valid but EMPTY reading for the block holding six of the eight courses (both passes), so the planted misread never had a row to change, the Intake agent re-read the wrong block (the one holding the totals) and made 2 refused `flag_cell` calls (field "GPA"). Per the rules: no retry, no tuning, not recorded. The Intake recheck therefore has no real-model demonstration tonight. Likely fixes (not done, outside this list): treat an empty reading of a block that has ink as a failed block; point the recheck at the block that lost the rows; stop the agent asking to flag a field that is not a cell.
- **Reading is not stable run to run.** The earlier B reads on the same file (before tonight) found all 8 rows; tonight's read found 2. Treat the vision path as the weak link and keep replay ready.
- **First result is not instant on A2** (16.7 s after Confirm) because the agent rows are queued before the mapped rows. Not changed (not on the list).
- **Auditor explanation slip** on C: it said "credits from 1 to 3"; the sheet already had 3. The proposal itself was right.
- **Skipped:** nothing on the list was skipped. Not done on purpose: no change to prompts or code to make a run pass.

## What is untested
- The Intake recheck against a real model producing a corrected value that passes (never happened).
- The planted-misread upload checkbox and the failed-block re-read path in a browser or with a real vision model (unit-tested only).
- Replay of the older field-test recordings after these changes (they use the legacy path; not re-run).
- The runbook's live timing on a cold model, and the UI under a real second user.
- Transcript A through the full live flow (by design: it must stay unprocessed). A2 is its stand-in and passed.
- Windows and Linux behaviour.

## Transcript A
`fixtures/demo_v3/transcript_a_live_demo.pdf` is byte-identical to the one committed before this run (sha256 `7e57a9f0f2600688e6e433627e0242724ae180ea1ff3fa8bf46ccf71f9c213eb`).
**It has no application records**: 0 applications in the local database by its file hash and by the student name or ID (Maya Okonkwo, SYN-1001), and none of the recordings mention it.

## Pushed state
Everything above is on `main` at the final commit; no force-push or history rewrite; nothing from `data/`, `.env` or any real transcript is tracked (checked with `git ls-files` before the final push).


## Agents-as-drivers change (not yet measured)
Code complete and covered by fake-provider tests (118 pass). **No real-model run of this version has been completed**: the B run, the clean/messy timing comparison and any recording are still to do. A2 was started once and stopped on request before it produced a result; nothing from it is recorded or reported. Transcript A was not touched.
