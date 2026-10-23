# CreditBridge

AI-assisted school-to-university credit evaluation prototype. **Synthetic data only. The AI suggests; a human decides.**
Spec: `CREDITBRIDGE_CLAUDE_BUILD_PLAN.md`. Decisions and measurements: `docs/decisions.md`.

## One-time setup (macOS)

```bash
brew install python node postgresql@18 ollama
brew services start postgresql@18 && createdb creditbridge     # this app's own state (local, never Supabase)
ollama pull qwen3.5:4b                                         # ~4 GB; supports vision + tool calls

git clone https://github.com/emoood/CreditBridge.git && cd CreditBridge
python3 -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'
(cd frontend && npm install)
cp .env.example .env        # then set DATABASE_URL (see below)
```

`.env` needs one secret, the **curriculum database** (teammate's Supabase, read-only). Ask the team for it privately and never commit it:

```
DATABASE_URL=postgresql+psycopg://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres   # Session pooler, port 5432
```

## Start everything (one command)

```bash
scripts/start.sh
```

It starts Ollama if needed, runs a **preflight** (it stops with a clear message if Postgres, Ollama, the model, the curriculum
database, the catalog or the frontend dependencies are missing), then starts the API (`:8000`) and the app (`http://localhost:5173`).
Ctrl+C stops both. Logs are in `data/logs/` and contain no transcript text. Check the setup alone with `python scripts/preflight.py`.

## Manual test script (about 10 minutes)

1. Open <http://localhost:5173>. The Evaluation page is the landing page; the yellow banner says the data is synthetic.
2. Click **Upload transcript** and choose `fixtures/demo_v2/school_transcript.pdf` (synthetic, image only).
3. **Watch phase 1 (reading).** Blocks turn from queued to running to done; a vision model reads one block at a time. Nothing is evaluated yet.
4. **Verify.** The scan appears beside the table. Check subjects, marks, grades, points and credits. Amber means uncertain; the warning bar lists cross-check flags (mark to letter to points, credit and GPA totals). `Moral Education` is shown as *Unrecognized · needs syllabus* because it is not in the curriculum database. Correct anything wrong; edits are logged.
5. Click **Confirm extraction and start evaluation.** This is the gate: evaluation never starts on its own, and clicking twice does not queue it twice.
6. **Watch phase 2.** Results appear one subject at a time, sorted by what needs your attention (blocked by a rule, contested, needs attention, ready to accept).
7. Open a row. See the matched outcomes side by side, the reviewer's objections, the policy checks and how the agents worked.
8. **Decide.** Accept or decline each suggestion (a reason is required to decline). Switch *Acting as* to *Registrar* to see the backend refuse a decision (403).
9. Optional: **Dev Console** (sidebar) shows the live model and tool calls.

If something stops mid-run (for example you restart the backend), the job is marked **INTERRUPTED**; click *Resume* and it continues from the last finished unit.

## Demo runbook (local model, synthetic data)

**Before the demo (about 2 minutes)**
1. `scripts/start.sh` starts Ollama if needed, runs the preflight, then the API (`:8000`) and the app (`http://localhost:5173`). The preflight alone: `python scripts/preflight.py` (stops with a clear message if Postgres, Ollama, the model, the curriculum database or the catalog is missing).
2. Warm the model: `python scripts/warmup.py` (the API also does this on start). It loads `qwen3.5:4b`, keeps it resident for 24 hours (`OLLAMA_KEEP_ALIVE`), sends one call and checks that thinking is off. Expect `"ok": true` and a warm call well under a second. If it says `ok: false`, start Ollama and rerun.
3. Open the app. The yellow synthetic-data banner must be visible. Pick "Admissions officer" in *Acting as*.
4. Check the recordings exist: *Replay a recorded run* lists `demo_a2` and `demo_c_copy_error`.

**Order of the demo**
| Step | Live or replay | What to show | Expected time |
|---|---|---|---|
| 1. Transcript A (Maya Okonkwo, SYN-1001) | **Live** | Type the name and ID, upload `fixtures/demo_v3/transcript_a_live_demo.pdf` (never upload it before the demo). The table appears (text layer, about 6 s). Click Confirm extraction. Nine courses map by code at once; AP Calculus AB and AP Computer Science A go to the Case agent (about 25 to 30 s on this Mac). | about 35 s |
| 2. Calculus AB | Live | The Case agent drafted a request for the AP score report and set it to *Waiting on document*. Click the row to open the evidence panel, type 5 in the *exam score* field and press *Hand back to the agent*. Only that row resumes: code re-checks the condition and it becomes Mapped with no model call. (A typed note alone, without the score, would send the row back to the agent.) | instant |
| 3. Finish the case | Live (code) | *Confirm mapped*, resolve Computer Science A (stale mapping) with Change or No credit, *Copy for spreadsheet*, then *Approve*. | about 1 minute |
| 4. Copy error and Auditor (Sofia Lindqvist) | **Replay** `demo_c_copy_error` | Load the replay, press Confirm. The purple PLANTED FOR DEMO banner, the failing sheet check, and the Auditor's explanation and proposed correction (before and after) are already there. Say it is a recording. | seconds |
| 5. The batch (Rania Haddad) | **Replay** `demo_a2` | The same courses as A with the Case agent's trace in the activity drawer, tagged *recorded*. | seconds |
| 6. Intake recheck | **No recording** | The planted-misread checkbox exists on the upload form, but the one real run on Transcript B failed (see OVERNIGHT_REPORT.md), so do not promise it live. Describe it from the extraction screen of any scan instead, or skip. | |

**Say out loud:** replays show the label "Replay of recorded run, <date>, <model>" at the top; the activity drawer marks recorded entries. Mapping codes and credits are placeholder data. The AI never approves; the officer does.

**If the live run fails** (the table does not appear, the Case agent does not answer, or the model is slow): do not retry on stage. Open *Replay a recorded run*, load `demo_a2` (identical courses to Transcript A), press Confirm, and tell the audience this is the recording of the same run. Everything after that step works the same, including submitting the Calculus exam score (that step is code, not a model). If the whole API is down: `scripts/start.sh` again; the database keeps the state, and an interrupted job offers *Resume*.

**Reset between rehearsals:** use Transcript A2 or a recording for rehearsals; Transcript A is only for the live run. Remove a rehearsal application by restarting from the worklist (they are harmless) or run `POST /api/v1/dev/reset` (dev only; it wipes all applications).

## Replay / Live AI (prepared runs, no model wait)

Header selector **Live AI / Replay** (Live AI by default; **Alt+D** switches). *Live AI*: the model reads and decides now. *Replay*: the model's answers come from a recorded run. A small "Replay" chip shows next to the synthetic-data banner, with a stage speed (**Fast**, about 0.5 s per stage, is the default; **Instant**) and **Reset** (clears every application so the demo can be repeated).

How it works: a prepared run is a finished real run saved at the model boundary (`fixtures/prepared_runs/<name>.json`, synthetic data only). An upload is matched to a prepared run by file hash, then by file name; nothing matches means the message "No prepared run for this file" and nothing is shown. The agents and all tools still run live in code, so row statuses, program-aware mapping, sheet checks, officer actions, copy and CSV are real; only the model's replies are recorded. Submitting a missing exam score resumes only that row (code, no model). Planted switches work: the copy error on any case, the misread on a case that has a misread prepared run. No Ollama and no network are needed (the curriculum links come from the prepared run). The activity drawer streams the agents' tool calls as they run, tagged *recorded*.

**A recording can go out of date, and then it is refused.** Each prepared run stores a `data_version` (a hash of everything in `policies/data/`: the AP mapping, the mapping conditions, the programme plans, the gen-ed categories; line endings do not matter) and a `reading_version` (the Intake prompt version and the block layout). If either differs from the code now, an upload in Replay answers **"Recording out of date: <why>. Re-record it, or switch to Live AI."** instead of replaying answers that no longer fit. `GET /api/v1/demo/recordings` lists every run with `current` and `reason`; in Replay the header shows "N recordings out of date" when any is. A recording made before versions were tracked counts as out of date. Live AI is never affected. After any change to `policies/data/` or to the reading, re-record (`scripts/overnight_runs.py`) before a demo.

**Names:** `demo_ap_<student>_<variant>`, the student's first name in lower case and the variant `clean`, `copy_error` or `misread`. Each planted-error switch is its own named run. `a2c` and `a2m` below are the `scripts/overnight_runs.py` arguments.

| Prepared run (`fixtures/prepared_runs/`) | Student, file | Variant | Shows |
|---|---|---|---|
| `demo_ap_rania_clean` | Rania Haddad, `transcript_a2_batch.pdf`, case CB-2026-0104 | clean (`a2`) | The batch case: eleven AP courses over two terms read by the Intake agent, mapping lookup, a conditional row waiting for an exam score, Case agent trace tagged *recorded*. **Out of date until Friend 2 re-records it** (recorded before versions were tracked and before the new block layout). |
| `demo_ap_rania_copy_error` | same file | copy error (`a2c`) | The same case with the Demo switch that changes one confirmed AUD code after the officer's decision: the sheet check fails and the Auditor explains the exact difference. Not recorded yet. |
| `demo_ap_rania_misread` | same file | misread (`a2m`) | The same case with one value (AP Chemistry's letter) replaced by a wrong one before the checks run; Intake must re-read it. Recorded only if the repair is genuine. Not recorded yet. |
| `demo_ap_daniel_clean` | Daniel Ferreira, `transcript_b_intake.pdf` / `.png`, case CB-2026-0102 | clean (`b`) | Image-only scan with a smudged mark (AP Chemistry printed 85): the check fails and the Intake recheck has work. Not recorded yet. |
| `demo_ap_sofia_copy_error` | Sofia Lindqvist, `transcript_c_copy_error.pdf`, case CB-2026-0103 | copy error (`c`) | Ordinary transcript; the Auditor catch after *Demo: plant a copy error*. Not recorded yet. |
| `demo_ap_rami_clean`, `demo_ap_rami_misread` | Rami Test-Student, `transcript_d_demo2.pdf`, case CB-2026-0105 | clean, misread (`d`, `dp`) | Second demo student. Not recorded yet. |

Make a prepared run: with the backend in demo mode, run the real model once through `python scripts/overnight_runs.py a2|a2c|a2m|b|c|d|dp`. It saves the run under the name above only when the run is clean by the script's criteria.

**Presenter script (order: D first, then C for the Auditor catch)**: *prepared runs exist for A2 only so far; C and D are in the plan below once they are recorded.*
1. Switch to Replay (Alt+D). Say: "The model's side is pre-recorded so we are not waiting on it; everything else is live."
2. Pick the case, upload its transcript. Say: "The Intake agent plans which blocks to read, reads them, and code checks every number."
3. Confirm extraction. Say: "Relevance gate: only externally examined qualifications. Mapping lookup: code. The Case agent works only the rows code could not settle and reviews the whole case."
4. Submit the exam score on the conditional row. Say: "Only this row resumes, and no model is involved."
5. *Confirm mapped*, then *Copy for spreadsheet*. Say: "Every export goes through the Auditor first."
6. For the Auditor catch (case C): after *Confirm mapped* press *Demo: plant a copy error*, then copy. Say: "One cell was changed after my decision. The sheet check catches it, and the Auditor proposes the fix; I decide."
7. Reset between runs; repeated runs give the same result (covered by a test).

## Recorded run (replay, so a demo never depends on the model)

After a real run reaches review, click **Save this run as a recording for replay** (or `POST /api/v1/recordings`). It is stored under
`data/recordings/<name>/` (gitignored, local only). **Replay a recorded run** loads it at the officer gate without calling the model; you can
pick up to two subjects to run live. Every replayed result is labelled `Replay of recorded run, <date>, <model>`; live ones are not.

## Demo transcripts (synthetic, invented students)

`fixtures/demo_v3/` (regenerate with `python fixtures/generate_demo_v3.py`; names and IDs in `students.json`, expectations in `expected.json`):

| File | Student | Use |
|---|---|---|
| `transcript_a_live_demo.pdf` (text layer) | Maya Okonkwo, SYN-1001 | The live demo case: most rows map; AP Calculus AB is conditional (needs an exam score) and AP Computer Science A is stale. Upload it fresh for the demo. |
| `transcript_b_intake.pdf` / `.png` (image only, no text layer) | Daniel Ferreira, SYN-1002 | One year block with a smudged mark (AP Chemistry, printed 85), so a check can fail and the Intake recheck has work. |
| `transcript_c_copy_error.pdf` (text layer) | Sofia Lindqvist, SYN-1003 | Ordinary transcript. After confirming, the footer switch **Demo: plant a copy error** (labelled "planted for demo") changes one confirmed AUD equivalent in the output stage; the sheet checks fail and the Auditor explains it. |

Type the student's name and ID in the boxes next to *Upload transcript*. All AUD codes and credits in the demo are **placeholder mapping data** (`policies/dummy_mappings.yaml`), not AUD's equivalencies.

## Checks

```bash
pytest -q                          # 66+ tests; fake model, in-memory DB; needs no Ollama, Postgres or Supabase
ruff check .
python scripts/e2e_live.py         # one live end-to-end pass through the API (needs the backend running)
```

## Notes

- Uploads are stored under `data/` (gitignored). **Never commit real transcripts.** Logs and audit events contain ids, counts and statuses only.
- `CATALOG=aud_2026_2027` uses the real AUD 2026-27 catalog (descriptions only, no learning outcomes); `CATALOG=demo` uses the small synthetic one.
- Model setup tried so far is in `docs/decisions.md`. The default is Ollama `qwen3.5:4b`; LM Studio is supported (`LLM_DEFAULT_ROUTE=lmstudio`).
- Deferred concerns (security, data depth, policy verification) are tracked in `docs/concerns.md`.
