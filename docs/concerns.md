# Deferred concerns (prototype first; revisit after the demo works)

User decision 2026-10-22: do not spend time on security hardening now. Track here, fix later.

## Security / access
- One shared owner-level Supabase password for everyone; no per-person roles.
- `public` schema holds views over `curriculum` with no Row Level Security; anon key may expose them.
- Direct DB connection fails on IPv6-less networks; using Session pooler. Revisit before choosing demo network.
- Role checks on the decision endpoint are simulated, not real auth.

## Data (curriculum DB, teammate's; swept 2026-10-22)
- Integrity clean: no orphans, duplicates, empty text, or missing hashes/URLs. 198 subjects, all `verified`.
- **Thin depth:** 232 objectives and 232 topics across 198 versions; 170 versions have exactly 1 of each. Real syllabi have dozens. Fine for a prototype, not enough for outcome-level matching.
- `assessment_components.weighting` is null in all 553 rows.
- `source_documents`: 38 rows but 37 distinct hashes for `official_ced` (one duplicate file).
- `public.*` are views of `curriculum.*` (identical). Intentional or not, ask teammate.
- School curriculum is NOT used by the agents in the demo (plan Section 16).

## Product
- Title-trap scenario defused by retrieval + matcher; demo must force the CS330 candidate to show the reviewer challenge.
- Reviewer failed to return a valid result on INFS230 (run 2); not yet investigated.
- Free-text excerpt ids in reviewer objections are not validated.
- Policy interpretations (e.g. C- fails "at least C") not verified by AUD staff.
- The real AUD 2026-27 catalog is loaded (601 courses) but has descriptions only, no learning outcomes; matching on description is weak and says so in the UI.
- 4B model quality: single runs only, no benchmark yet.

## Repo / setup
- `db/models.py` now holds only this app's 5 tables (local PostgreSQL). Nothing from this app is created in Supabase.
- Windows/Linux behavior of Ollama structured output untested.

## Added 2026-10-22 (background processing)
- App state is in a local PostgreSQL database `creditbridge` (not Supabase). Anyone running the app needs it created (`createdb creditbridge`); `scripts/start.sh` checks this.
- The school grade scale used for cross-checks is a synthetic assumption; a real school's scale may differ and cause false flags.
- A trailing footer block can fail to read (harmless, flagged `BLOCKS_FAILED`); the UI warning wording could confuse an officer.
- Evaluation of one transcript takes 6-10+ minutes with the 4B model; use a recorded run (built: README, data/recordings) for live demos.
- The reviewer now only advises (no revision round). It still raises noise objections (policy-engine results, credit counts) despite the prompt; officers see them next to the pick.
- Real transcripts must never be committed: `data/` is gitignored; demo1.pdf/demo2.pdf were moved to `data/demo_inputs/`.

## Added 2026-10-22 (field test, see docs/decisions.md)
- matching_v4 still tells the matcher to call lookup_precedent, which is no longer registered until precedents exist; harmless but wastes a turn. Fix in the next prompt version (not edited per instruction).
- The synthetic grade scale has no numeric marks, so every real numeric mark (e.g. 98) makes AUD_GRADE_LOWER say NEEDS_REVIEW; the reviewer then repeats it as an objection and every match shows "a policy rule needs human review".
- Title-only suggestions include weak guesses (a fallback course, a capstone). They are labelled unverified and cannot be accepted, but they are noise.
- Recordings and field-test output live in data/ (gitignored). They hold a real student's transcript: keep them local.
