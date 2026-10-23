Read docs/OWNERSHIP.md and docs/REQUIREMENTS.md (sections 0, 1 and 10) first. Chunk C1b = C1 step 5 (recordings). C0, C1 steps 1 to 4 and C2 are merged. Follow the Git, Rules and Change log sections in OWNERSHIP.md.
This chunk makes live model runs, on purpose: one real run per recording, no retries, no tuning of prompts or code to get a good result, synthetic data only. Everything else is code you only read. Only edit the files listed under Step 4.
Audit first (a few lines), then run in order, one commit at the end of each step. Labels: [CODE] deterministic, [AGENT] model-driven with code verifying.

Scope: one student, Rania (case CB-2026-0104, BBA Finance, fixtures/demo_v3/transcript_a2_batch.pdf), AP only. Skip Daniel, Sofia and Rami.

Before you start (do not skip)
- git pull --rebase origin main. Read docs/changes/INDEX.md. If a C3 data change is still pending, STOP: recordings must be made after policies/data/, the Intake prompt and the block layout are final, because any later change marks them out of date (data_version / reading_version).
- Ollama is running with qwen3.5:4b (or the model Emad names; the recording stores which model made it). API up with DEMO_MODE=true (the default), so model replies are captured.
- python scripts/warmup.py, then curl -X POST localhost:8000/api/v1/dev/reset for a clean database. Stay off the machine during a run (other work skews the timings).

Step 1. Audit (a few lines)
- Confirm scripts/overnight_runs.py has the modes a2, a2c, a2m and the names demo_ap_rania_clean, demo_ap_rania_copy_error, demo_ap_rania_misread. Confirm prepared.save stamps data_version, reading_version, model and date. Confirm the existing fixtures/prepared_runs/demo_ap_rania_clean.json is the old, out-of-date one (it will be replaced).
- Note the current data_version (GET /api/v1/demo/recordings) so you can show it changes nothing during the chunk.

Step 2. [AGENT] Record, one run each
| Command | Saved as | Saved only if |
| python scripts/overnight_runs.py a2 | demo_ap_rania_clean | Human Geography is Waiting on document with a drafted syllabus request; a score of 5 on Statistics changes only that row; no refused tool call |
| python scripts/overnight_runs.py a2c | demo_ap_rania_copy_error | the Auditor's explanation matches the verified difference (same row, both codes, exactly one difference) |
| python scripts/overnight_runs.py a2m | demo_ap_rania_misread | the Intake agent really repaired the planted value (AP Chemistry letter B+ replaced by C+, then re-read correctly and passing its check) |
A run that fails its check saves nothing. Do not rerun it to fish for a good result: record it in the log and report it. Each saved run lands in fixtures/prepared_runs/ by itself; the Run picker shows it without restarting the API.

Step 3. [CODE] Verify in Replay (no model)
- Toggle to Replay. For each saved run: upload the file, confirm the extraction, and check the rows match the live run. The "recording out of date" chip must be gone and the run listed as current in GET /api/v1/demo/recordings.
- Clean: Human Geography Waiting on document; a score of 5 on Statistics resumes only that row; Chemistry vs Physics 1 and Art History vs Human Geography show as candidates and Choose resolves them.
- Copy error: planted switch on, the checks catch it, the Auditor explains the verified difference and offers the fix.
- If something differs between live and Replay, report it; do not edit code in this chunk.

Step 4. Files and docs (the only files you may edit)
- fixtures/prepared_runs/*.json (the new recordings; never anything under data/, never .env).
- README.md: replace the old run names; add the table "file, what you will see, what it proves" for each recording (REQUIREMENTS.md section 1).
- docs/MODEL_TESTING.md: replace the old names (demo_a2 and so on); add the three commands above.
- docs/changes/C1b.md (change log), one line in docs/changes/INDEX.md, the C1 row in OWNERSHIP.md set to DONE and a C1b row added, then push to main.

Rules for this chunk
- Real runs only through scripts/overnight_runs.py. One run per recording. No prompt, data or code edits to improve a result; if a run fails or looks wrong, say so.
- After this chunk, any change to policies/data/, the Intake prompt (ADDENDUM) or the block layout needs a re-record: say so at the end of the change log.
- Plain wording in docs: no "need you", no "stale".

Finish: change log docs/changes/C1b.md, a line in INDEX.md, the OWNERSHIP rows, push to main. Final report under 15 lines: which recordings were saved and which were not (and why), the model, timings per stage and time to the first table (the last measurement was 50 s, target under 25 s), vision read time per page, each row's final status, whether Computer Science A was read, whether the header fields (Institution / curriculum, Major) came out right, and any difference between live and Replay.
