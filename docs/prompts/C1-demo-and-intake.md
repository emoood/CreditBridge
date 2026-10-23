Read docs/OWNERSHIP.md and docs/REQUIREMENTS.md (sections 1, 2, 3, 5) first. Chunk C1, branch C1. C0 is merged. Follow the Git, Rules and Change log sections in OWNERSHIP.md. Model runs use the local model (Ollama); do not use a paid API; retry a recording at most once.
Audit first (a few lines). Reuse what exists. One commit per step, in this order. Labels: [CODE] deterministic, [AGENT] model-driven with code verifying.

Step 1. [AGENT] Intake speed
Today the clean demo_a2 run takes about 50 s reading (roughly nine vision reads at about 5.6 s each). Find out from list_pages why there are that many blocks and which carry no course rows (headers, footers, legend, grading scale). Do not read blocks that have no course rows; merge small blocks if that is safe. Target: reading under 25 s on the clean transcript with the same extraction result. Keep the text-layer/code OCR as cross-check only. Report before and after timings.

Step 2. Extraction header, major, credits

- [AGENT fills] Add the field "Institution / curriculum" at the top of ExtractionCheck, filled by Intake from the document (AP, IB, A Level, French Bac, or a university name). Student type (school or university) follows from it. Keep the check against the case record's curriculum and the name check.
- [AGENT] Add a "Major" field, extracted for university transcripts; schools have none. If a university transcript shows no major, mark it missing.
- Credits: school transcripts show credits only if printed (the school_credits display flag exists). University transcripts require credits; if missing, mark the row "Credits missing" (if request items from chunk C2 are merged, raise one; otherwise just mark it).

Step 3. Per-field edit and re-evaluate
Edit already exists (patchRow, EDITABLE fields in backend/app/api/intake.py). Keep it. Add Re-evaluate per field: [AGENT] re-read only that cell (crop_region at higher zoom through read_block) without showing the agent the old value; the officer sees old and new side by side and picks. [CODE] then recomputes dependents (checks, grade eligibility, mapping lookup, placement, totals) with no model call. Menu options "Re-read this row" and "Re-read this page"; never an automatic whole-document re-run. Officer-edited fields are never overwritten by AI; they show "edited by officer" (who, when) and re-evaluating one asks first. Same budget and tool-call ledger as other agents.

Step 4. Demo mode
Today: header checkbox "Demo", Alt+D, "Demo mode" chip, stage speed, Reset; prepared runs in fixtures/prepared*runs matched by sha256 or filename.
Change: relabel the control as Replay / Live AI (same behavior, same screens, same case list; the chip stays small). Add a data_version to each prepared run (hash of mapping_ap.csv, mapping_conditions.yaml and the plan files in policies/data/) and refuse Replay with "recording out of date" on a mismatch. Case names follow demo_ap*<student>\_<variant>; add a README table of what each file shows. Variants: clean, copy error (a2c), misread (a2m). Planted-error switches are separate named variants.

Step 5. Recordings (last; policies/data/ must be final, see C3 data step)
Re-record the clean case for Rania (Finance), then the planted copy error. The planted misread only if the repair is genuine. Use scripts/overnight_runs.py if it fits. Then check: Human Geography shows Waiting on document and resumes on a score-5 style upload; the Auditor explanation matches the verified diff. Report timings per stage. Commit the recordings under fixtures/prepared_runs/ (never anything under data/).

Finish: change log docs/changes/C1.md, a line in INDEX.md, C1 row status, pull request per OWNERSHIP.md. Final report under 20 lines: audit result, timings before and after, recordings made and their variants, anything you could not do.
