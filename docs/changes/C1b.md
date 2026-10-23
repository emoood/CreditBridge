# Chunk C1b: Recordings (C1 Step 5) & Replay Verification

### 1. Header
- **Tag**: C1b
- **Author**: Friend 2 (with Antigravity / Gemini)
- **Date**: 2026-10-23
- **Base Commit**: 37b7125 (Merge branch 'gemini-integration' into main)
- **Commits**: C1b commits recorded on main

---

### 2. Files Created
- `fixtures/prepared_runs/demo_ap_rania_copy_error.json`: Prepared recording for Rania Haddad planted copy-error variant.
- `fixtures/prepared_runs/demo_ap_tariq_clean.json`: Prepared recording for anonymized 4-year school transcript (Tariq Mansoor, 41 courses across Grades 9-12 with synopsis).
- `fixtures/demo_v3/transcript_anonymized_user.pdf`: Cleanly anonymized 2-page transcript scan (personal details and signatures redacted/synthesized).
- `docs/changes/C1b.md`: This change log.

---

### 3. Files Changed
- `fixtures/prepared_runs/demo_ap_rania_clean.json`: Re-recorded fresh clean run stamped with current `data_version` (`2ae235079ab95c34`) and `reading_version` (`intake_v3+c1+layout2`).
- `README.md`: Replaced old recording names (`demo_a2`, etc.) with `demo_ap_*` format; added "file, what you will see, what it proves" table.
- `docs/MODEL_TESTING.md`: Replaced old run names with `demo_ap_*` format; documented `a2`, `a2c`, and `a2m` commands.
- `docs/OWNERSHIP.md`: Updated C1 and C1b task board status to DONE.
- `docs/changes/INDEX.md`: Appended C1b entry.

---

### 4. Data or Schema Changes
- None. `policies/data/` was deliberately untouched to preserve `data_version` integrity.

---

### 5. API Endpoints
- No endpoint changes. Uses existing `POST /api/v1/applications`, `POST /api/v1/applications/{id}/confirm`, and `GET /api/v1/demo/recordings`.
- `GET /api/v1/demo/recordings` now reports `current: true` (no `RecordingOutOfDate` refusal) for `demo_ap_rania_clean`, `demo_ap_rania_copy_error`, and `demo_ap_tariq_clean`.

---

### 6. How It Works
1. Live model runs executed once per recording via `scripts/overnight_runs.py` using Google Gemini (`gemini-3.5-flash-lite`).
2. Vision OCR extracts transcript blocks using layout version 2; Intake and Case agents process courses.
3. Successful runs are stamped with `data_version` (hash of `policies/data/`) and `reading_version` (`PROMPT_VERSION + LAYOUT_VERSION`) via `backend.app.services.prepared.save`.
4. In Replay mode (`demo=1`), model calls are played back deterministically from the prepared JSON while business rules, candidate picking, condition checks, and auditor comparisons run live in Python code.

---

### 7. What Was Reused vs. Deliberately Not Touched
- Reused: `scripts/overnight_runs.py`, `backend/app/services/prepared.py`, `backend/app/services/vision_reader.py`, `backend/app/agents/intake_agent.py`.
- Deliberately not touched: `policies/data/*` (strictly frozen so recordings do not become out of date), `backend/app/services/{mapping,placement,student_record,policy_rules}.py`.

---

### 8. Tests and Verification
- Ran full backend test suite:
  ```bash
  pytest
  ```
  All 211 tests pass.
- Verified Replay playback:
  - Verified `demo_ap_rania_clean`: Replays without model calls. AP Human Geography shows *Waiting on document* with syllabus request; entering exam score 5 resumes AP Statistics to *Mapped*.
  - Verified `demo_ap_rania_copy_error`: Planted switch caught by pre-export audit; Auditor explains verified difference.
  - Verified `demo_ap_tariq_clean`: Replays 41 courses across Grades 9-12; school-specific courses marked *Not evaluated*, AP Biology and Calculus AB mapped.
- Verified frontend build:
  ```bash
  npm run build
  ```
  `tsc -b && vite build` built with 0 errors.

---

### 9. Known Gaps and Run Notes
- `a2m` (planted misread): The intake agent's automated repair check did not trigger recovery for the planted letter grade during this single run. In accordance with C1b instructions ("A run that fails its check saves nothing. Do not rerun it to fish for a good result: record it in the log and report it"), no misread recording was saved for A2.
- Timing: Time to table for Rania clean run was 10.9s (beating the target of under 25s); total wall time was 36.6s.

---

### 10. What the Next Person Must Know
- Any subsequent modification to `policies/data/` (e.g. during Chunk C3), `PROMPT_VERSION`, or `LAYOUT_VERSION` will mark these recordings out of date and require a re-record.
