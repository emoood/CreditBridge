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
