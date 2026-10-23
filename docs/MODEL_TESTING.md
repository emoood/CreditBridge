# Model testing guide (for teammates running this on their own machine)

Purpose: record **prepared runs** with a real local model so Demo mode can play them back instantly, and find out how well the model reads the synthetic transcripts. Demo mode itself never calls a model, so none of this is needed to *give* the demo; it is needed to *add or refresh* a case in the demo.

You can do this by hand, or paste the prompt at the end into Claude Code (or any coding agent) opened in the repository.

## Rules (read first)
- **Synthetic data only.** Use the files in `fixtures/demo_v3/` (and `data/demo_inputs/demo2.pdf`, which is the same synthetic file as `transcript_d_demo2.pdf`). Never upload a real student document.
- **Never upload `transcript_a_live_demo.pdf` before the live demo.** It is reserved for the live run. Use `transcript_a2_batch.pdf` (same courses, different invented student) for everything else.
- **Local models only.** No cloud APIs, nothing exposed to the network. Do not commit or print `.env` (it holds a read-only curriculum database URL).
- **Never touch the curriculum database** except to read. The app only reads it.
- **One run per case, no retries to "get a good one".** Report what happened. Do not tune prompts or code to make a run pass. If a run is bad, say so and stop; fixes are a separate piece of work.
- **Never edit a prepared run by hand.** The file is the model's raw replies. Hand-written replies would be invented output.

## Setup
```bash
cd creditbridge
python -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'   # see README for the full one-time setup
ollama pull qwen3.5:4b          # the model used for the recorded runs (qwen3.5:9b is the optional stronger one)
pytest -q && ruff check .       # all tests must pass before you start; they use a fake model, no Ollama needed
```
Start the stack with `scripts/start.sh` (Ollama, Postgres, API on :8000, app on :5173). Check `curl localhost:8000/api/v1/health` shows `"provider":{"ok":true,...}`. Warm the model: `python scripts/warmup.py` (expect `"ok": true`).

The API must be in demo mode for runs to be captured (it is by default: `DEMO_MODE=true`). Capture stores the model's raw replies for synthetic applications only.

## The runs
Driver: `scripts/overnight_runs.py <case>`. It uploads the file through the real API, waits for each job, drives the officer steps, collects timings from the usage ledger and, **only if the run is clean**, saves it as a prepared run in `fixtures/prepared_runs/<name>.json`. The full log goes to `data/overnight/<case>.json` (gitignored).

| Case | Command | Student / file | Saved as | What "clean" means |
|---|---|---|---|---|
| A2 | `python scripts/overnight_runs.py a2` | Rania Haddad, `transcript_a2_batch.pdf`, case `CB-2026-0104` | `demo_a2` | all 11 rows read; no refused tool call; AP Calculus AB waits on a document, then Mapped after the exam score; export audit clean |
| C | `python scripts/overnight_runs.py c` | Sofia Lindqvist, `transcript_c_copy_error.pdf`, `CB-2026-0103` | `demo_c` | the planted copy error is caught by the normal sheet check and the Auditor's proposal restores the original equivalent |
| D | `python scripts/overnight_runs.py d` | Rami Test-Student, `transcript_d_demo2.pdf`, `CB-2026-0105` | `demo_d` | see "D" below |
| D, planted misread | `python scripts/overnight_runs.py dp` | same file, misread switch on | `demo_d_misread` | saved only if the Intake agent **really repaired** the planted value (re-read value passes its check) |
| B (optional) | `python scripts/overnight_runs.py b` | Daniel Ferreira, `transcript_b_intake.pdf` (image only) | `demo_b_intake` | saved only if the planted misread was really repaired |

Run them one at a time (the app has one worker). A2 takes about 2 to 3 minutes on an M-series Mac with the 4B model; D takes longer (about 14 blocks to read).

### D: what to check row by row
Expected shape of the result (from the fixture and the policy files):
- Grades 9 and 10: all **Not evaluated** ("Not in curriculum"). Do not link a plain "Chemistry" or "Biology" to AP.
- School-specific courses (Arabic A, Islamic Education, Moral Education, Physical Education, Social Studies): **Not evaluated, school-specific**, never mapped, never an elective.
- Grades 11 and 12, AP courses only are evaluated: AP Chemistry, AP Computer Science A, AP Macroeconomics, AP Statistics, AP Calculus BC, AP English Language, AP Psychology.
  - AP Calculus BC: conditional on an exam score of at least 4 (Waiting on document; Mapped after score 5).
  - AP Computer Science A: stale mapping (Conditional).
  - AP English Language: blocked by policy (ENGL never transfers).
  - AP Macroeconomics: classed **General elective suggestion** for the engineering program.
  - AP Chemistry and AP Macroeconomics have no entry on the synopsis page, so they **wait on their syllabus**.
- The synopsis page (page 2) is the syllabus excerpt for AP Computer Science A, Statistics, Calculus BC, English Language and Psychology.
- Row count: 41 rows on page 1 (11 + 10 + 10 + 10) in four year tables; the grade totals printed under each table must match.

**Known problem (open):** the first real D run with the 4B model read only 26 of 41 rows. Grade 11 course names were lost or shortened, and five rows came back without a subject. The cause looks like the diagonal watermark and pale table fills over the grade 11 table; the 9B model failed on the same blocks. A background-whitening step was added but not confirmed. If your run has this problem, **do not record it**; report it with `scripts/read_test.py` output (below). Ideas to try as a separate task: higher resolution for the narrow column crops, stronger watermark removal, smaller blocks, a different vision model.

### Diagnosing reading quality (one model call per block)
```bash
python scripts/read_test.py mac_dev data/demo_inputs/demo2.pdf 1 1 2 3 4     # page 1, blocks 1..4
python scripts/read_test.py mac_quality data/demo_inputs/demo2.pdf 1 3 4     # the 9B model
```
It prints subject, mark, letter and credits for each block and the printed totals.

## After a run
1. Read `data/overnight/<case>.json`: `problems`, `clean`, `steps` (timings), `calls_per_stage`, `refused_tool_calls`, `states_final`, `extraction.intake` (recovery rounds, repairs, blocks read by code, name check).
2. Check **every row** against the expected shape above. A row that is wrong is a finding, not something to patch.
3. If clean, the prepared run is already in `fixtures/prepared_runs/`. Verify Demo mode with **no model** (start the API with `WARMUP_ON_START=false` or stop Ollama):
   ```bash
   WARMUP_ON_START=false uvicorn backend.app.main:app --port 8000 &
   python scripts/demo_flow.py fast    transcript_a2_batch.pdf CB-2026-0104
   curl -X POST localhost:8000/api/v1/dev/reset
   python scripts/demo_flow.py instant transcript_a2_batch.pdf CB-2026-0104
   ```
   `live model calls` must be 0 and the printed rows identical in both runs. Reference figures (A2): fast mode table ready in about 7 s and evaluation done in about 15 s; instant mode 0.5 s and 2.7 s.
4. Commit only `fixtures/prepared_runs/*.json` (synthetic, labelled with date and model) and notes. Never commit `data/`, `.env` or a real document. Update the report section below in `OVERNIGHT_REPORT.md` with what you ran, the model, the timings and each row's final status.

## Reference results so far (qwen3.5:4b, Ollama, thinking off, M-series Mac)
| Run | Result |
|---|---|
| A2 | clean, recorded as `demo_a2`. Table ready 86 s (4 vision calls 34 s for the page, 14 Intake agent calls 48 s). Mapping lookup instant. Case agent 26 s plus review 17 s. Auditor on export 4 s. 32 model calls. |
| B | one run, not recorded: an empty block was repaired, the planted misread was not (both recovery rounds went elsewhere). |
| D | one run, not recorded: only 26 of 41 rows read. |
| C | not re-run since the agents-as-drivers change (an earlier run of the older flow was clean). |

Per vision call 4 to 13 s per block; vision now reads every upload including digital PDFs, so reading is much slower than the old text-layer path (about 6 s for A2). That is the cost of "vision is the reader".

## Prompt to paste into Claude Code (or equivalent) on your machine
> You are in the CreditBridge repository. Follow `docs/MODEL_TESTING.md` exactly. Use synthetic data only; never upload `transcript_a_live_demo.pdf`; never print or commit `.env`; do not tune prompts or code to make a run pass; do not retry a run; do not hand-edit prepared runs. First run `pytest -q` and `ruff check .` and report the result. Check the health endpoint and warm the model. Then run, one at a time, `a2`, `c`, `d`, and `dp` with `scripts/overnight_runs.py`. After each run read `data/overnight/<case>.json`, check every row against the expected shape in the guide, and report: the model, total time, time per stage (Intake, mapping, Case, Auditor), vision time per block, model calls per stage, refused tool calls, each row's final status, and whether the run was saved. If a run is bad, do not record it: run `scripts/read_test.py` on the failing blocks and report the output. Finally verify Demo mode with `scripts/demo_flow.py` twice (with a reset between) and confirm zero live model calls and identical rows. Commit only `fixtures/prepared_runs/*.json` and your notes, and push to a branch, not to main.

## Note on the A2 prepared run (code changed after it was recorded)
`demo_a2` was recorded before two reading fixes: credits that the model filed under "points" now move to credits when the letter grade proves it, and totals the model reports for a page whose text layer prints none are dropped. Because Demo mode plays the model's replies through live code, the replayed extraction is clean (11 rows, no flags). The activity drawer still shows four refused `flag_cell` calls, because the recorded agent flagged cells that no longer fail. Re-record A2 with the current code (`python scripts/overnight_runs.py a2`) to remove them.

## Note: Rania's demo data changed after `demo_a2` was recorded
For Rania (BBA) AP Statistics is now conditional on an exam score of at least 4 and AP Microeconomics has a stale mapping; AP Calculus AB and AP Computer Science A are now "Not applicable" (engineering areas are restricted for BBA programs). The recorded Case agent replies do not cover the two new open rows, so in Demo mode those rows use the code's verdict and sentence (the review modal never shows raw agent text). Re-record A2 to capture the agents' work for the new situation: `python scripts/overnight_runs.py a2`.

## Note: classification changed to policies/program_constraints.yaml
Each mapped course is now classed, in order: **Not applicable** (blocked area or blocked course for the program), **Core course** (in the program's core list), else **General elective suggestion**. There is no allowed-elective list, no credit cap, no "Not in the plan" label and no elective judging by the Case agent (the `set_classification` tool and the `program_mismatch` doubt are gone). The Case review prompt (`case_v3.txt`) lost its elective step, so any recording made before this change differs from a fresh run. The review list is grouped (Core courses, General electives).
Recordings made before this change: `demo_a2` (the only one). Re-record A2, and record C and D, after the real-model runs are approved.
