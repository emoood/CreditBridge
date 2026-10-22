# Overnight report (updated after every part)

Rules followed: work on `main`, push after every part, no force-push, tests and lint green before each push, synthetic data only, local model only.
Rollback point: tag `pre-overnight` = `3801f36` (the last commit before this run).

## Status
| Part | State | Commit |
|---|---|---|
| Pre: push unpushed commits, tag `pre-overnight` | done | `3801f36` (tag) |
| 0. Delete the degraded B recording and files | done | `4711876` |
| 1. Local model speed set-up | done | `5b8693f` |
| 2. Transcript A2 (A untouched) | done | `0c6072e` |
| 3. Planted misread switch, Intake re-reads failed blocks, term-total questions | done | (this commit) |
| 4. Real runs of A2, B (switch on), C | | |
| 5. Replay mode, live/replay per application | | |
| 6. README demo runbook | | |
| 7. Small tests | alongside each part | |

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

## Next
Part 4 (real runs).
