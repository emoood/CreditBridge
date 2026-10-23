Live run test: demo1 and demo2 transcripts through the real pipeline. Follow docs/OWNERSHIP.md rules (push straight to main, consumption limits, change log). Do not change application code in this task except a provider file if one is missing (step 1). Labels: [CODE] deterministic, [AGENT] model-driven.

Goal: put each real transcript in, time the output, and check it against the correct answer. No replay, no recording checks.

Step 1. Audit and setup (a few lines)

- Find the demo1 and demo2 PDFs and any ground-truth or expected-output files for them (look in fixtures/ first; list what you find, do not assume names).
- Check backend/app/providers for a Gemini provider. If it is missing, add one in a new file only, wired through the existing provider setting, with the key read from .env (never committed, empty key in .env.example).
- Start the app on the server in Live AI mode (Demo / Replay off). Record the model name and version used.

Step 2. Correct answer for each PDF

- If a ground-truth file exists for a transcript, use it.
- If not, write one: read the PDF yourself and list every course row (course name, code if printed, grade or score, credits if printed, year/term) in a CSV at docs/test_runs/<pdf-name>\_expected.csv. Do not copy it from the model's output. Mark any cell you could not read as UNREADABLE.

Step 3. Run
Upload each PDF through the normal upload path (the same API/UI path a real officer uses), one run per PDF, nothing special set. Capture:

- time for each stage (Intake read, Case work, Auditor) and the total, from the app's own timing or timestamps
- number of model calls per stage and any retries or errors
- the full output: extracted rows, statuses, placements, credit totals, Auditor findings, saved as JSON in docs/test*runs/<pdf-name>*<model>\_<date>.json

Step 4. Compare [CODE]
Write one small script (scripts/compare_run.py) that compares extracted rows to the expected CSV and prints, per PDF: rows expected, rows read, rows exactly correct, and every difference as "expected X, got Y" (course, grade, credits, missing row, extra row). Also print any row where the status or placement looks inconsistent with the mapping table (for example Mapped with no AUD code). Do not auto-fix anything.

Step 5. Report (under 20 lines, saved to docs/test_runs/REPORT.md and sent to Emad)
Per PDF: model, total time, time per stage, rows expected / read / correct, the list of differences, errors or retries, and anything surprising. Then one line each: is the Intake reading good enough to demo, and where the time goes. No recommendations beyond that unless a difference is systematic (the same mistake repeating), in which case name the pattern.

Rules

- Run each PDF once. Run it a second time only if the first failed with an error, and say so.
- Do not tune prompts or change extraction code during this task. Report problems; Emad decides the fix.
- Never commit .env, keys, or real student data. The demo PDFs are invented students.
- Write docs/changes/TEST.md with the usual change-log points (files created, how to rerun), add a line to docs/changes/INDEX.md, push to main.
