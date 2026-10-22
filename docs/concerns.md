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
- Real AUD catalog CSV (no outcomes) still to arrive; matching on description only will be weak.
- 4B model quality: single runs only, no benchmark yet.

## Repo / setup
- `models.py` (17 tables) is defined and tested on SQLite but intentionally not applied to Supabase.
- Windows/Linux behavior of Ollama structured output untested.
