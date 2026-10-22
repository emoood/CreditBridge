# Overnight report (updated after every part)

Rules followed: work on `main`, push after every part, no force-push, tests and lint green before each push, synthetic data only, local model only.
Rollback point: tag `pre-overnight` = `3801f36` (the last commit before this run).

## Status
| Part | State | Commit |
|---|---|---|
| Pre: push unpushed commits, tag `pre-overnight` | done | `3801f36` (tag) |
| 0. Delete the degraded B recording and files | done | `4711876` |
| 1. Local model speed set-up | done | `5b8693f` |
| 2. Transcript A2 (A untouched) | next | |
| 3. Planted misread switch, Intake re-reads failed blocks, term-total questions | | |
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

## Next
Part 2.
