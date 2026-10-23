# Ownership, task board, git workflow and change log rules

Every Claude Code session reads this file first. Work on ONE chunk at a time. Do not edit files outside that chunk's list.

Start a chunk: "Read docs/OWNERSHIP.md, then do chunk <TAG> as described in docs/prompts/<TAG>.md." Steps inside a chunk run in the order written. Work on a branch named after the tag.

## Task board

| Tag | Chunk                                                                                                                                        | Owner                                | Status                          | Must wait for                    | Prompt                                     |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ | ------------------------------- | -------------------------------- | ------------------------------------------ |
| C0  | Real data, placement, review list (mapping_ap.csv, nine plans, placement.py, propose_placement)                                              | Emad                                 | DONE (commits 154aef5, 9d5a4fb) | none                             | none                                       |
| C1  | Intake and demo: speed, extraction header, credits, per-field re-evaluate, Replay / Live AI toggle, recordings                               | Emad                                 | TODO                            | C0                               | docs/prompts/C1-demo-and-intake.md         |
| C2  | Requests: request items, real email sending, replies, syllabus comparison, add to mapping                                                    | Friend                               | TODO                            | C0                               | docs/prompts/C2-requests-email-syllabus.md |
| C3  | Data and other curricula: elective lists, grade tables, IB / A Level / French Bac mappings (data first, then code), university transfer path | Emad (data with ChatGPT), code later | TODO                            | data: none. code step: C1 merged | docs/prompts/C3-data-and-curricula.md      |

## What can run at the same time

- C1 and C2: yes, in parallel. They use different files. The only shared files are models.py, main.py, api.ts and case_tools.py, and each chunk only appends its own lines.
- Inside C1: speed, then header/credits/re-evaluate, then toggle, then recordings last (the header changes the extraction output, so record after it).
- Inside C2: request items, then email, then replies, then syllabus comparison (it needs a RECEIVED request).
- C3 data step: any time, but it must be merged BEFORE C1's final recordings. policies/data/ is part of each recording's data_version, so changing it after recording marks the recordings out of date.
- C3 code step (grade scales, major field, university transfer): only after C1 is merged, because it touches the same extraction code.
- The demo is presented from a tagged commit (demo-2026-10-24), not from main, so nothing merged later can break it.

## File ownership

- C1: backend/app/agents/intake_agent.py, backend/app/api/intake.py, backend/app/services/{vision_reader,extraction,extraction_checks,prepared,recording}.py, frontend/src/{ExtractionCheck,DemoControls}.tsx, fixtures/prepared_runs/, fixtures/demo_v3/, scripts/overnight_runs.py, docs/README table for demo cases
- C2: new files backend/app/services/{outbox,syllabus_compare}.py, backend/app/api/outbox.py, backend/app/agents/prompts/syllabus_v1.txt, frontend/src/Outbox.tsx, backend/tests/unit/{test_outbox,test_syllabus_compare}.py. One-line appends only: backend/app/db/models.py, backend/app/main.py, frontend/src/api.ts, frontend/src/StudentPopup.tsx, backend/app/agents/tools/case_tools.py (register one tool), .env.example (empty SMTP keys)
- C3: policies/data/ (data step), policies/school_grade_scales.yaml and backend/app/services/grade_checks.py (code step)
- Emad only; everyone else must not edit: backend/app/services/{mapping,placement,student_record,policy_rules}.py, policies/data/ (outside C3), policies/demo_students.yaml, frontend/src/{Review,model}.ts(x)

## Git: commit and push

1. Start: `git checkout main && git pull` then `git checkout -b <TAG>` (for example C2).
2. Commit small and often. Stage only files in your chunk's list: `git add <paths>` (not `git add .`), check with `git status`, then `git commit -m "<TAG>: <step>: <what changed>"`.
3. Never commit .env, credentials, real student data, or local model recordings under data/. Never reformat files.
4. Push your branch: `git push -u origin <TAG>`. Never push to main directly.
5. Before the pull request: `git fetch origin && git rebase origin/main`, run the full `pytest -q`, then `git push --force-with-lease` (only on your own branch).
6. Open a pull request into main (`gh pr create --base main --title "<TAG>: <summary>" --body "see docs/changes/<TAG>.md"`). Emad merges, one pull request at a time. If a rebase shows a conflict in a file outside your list, stop and ask Emad.
7. After merging, Emad tags the demo commit: `git tag demo-2026-10-24 && git push origin demo-2026-10-24`.

## Rules

- Run only the tests for what you changed; full pytest -q once before the pull request.
- Shared files: append your own lines only. Never reformat, reorder or rename existing code.
- Wording in the UI and in emails: plain and short. Never "need you", never "stale".
- Limit consumption: grep and read only the needed line ranges, no whole-file reads, no re-reading, no refactors outside your chunk, short commits, no long narration, final report under 20 lines. If something fails twice, stop that part, note it, move on.
- Audit first: a few lines saying what exists (file and function) and what does not. If a file or feature named in a prompt is not as described, say so and do not invent it.

## Change log rule (every chunk, every person)

Before finishing, write docs/changes/<TAG>.md describing exactly what you did. It is for whoever picks up the next chunk, so be precise, not a summary. Include:

1. Tag, branch, author (person name and which AI tool), date, base commit, and the commit hashes that make up the chunk.
2. Files created (path, one line on what it does).
3. Files changed (path, what changed and why, with function or class names).
4. Data or schema changes (tables, columns, config keys, .env.example entries, policy or data files).
5. API: new or changed endpoints, request and response shape.
6. How it works in 5 to 10 lines: the flow, which functions call which.
7. What you reused and what you deliberately did not touch.
8. Tests added or changed, and how to run them.
9. Known gaps, assumptions, things you could not verify.
10. What the next person must know before editing the same area.
    Add one line to docs/changes/INDEX.md (date, tag, branch, summary) and update only your own row's Status on the board.

## Scope check before every pull request

Run `git diff --name-only origin/main` and compare it with your chunk's file list. Every file outside the list goes in the change log with the reason. Files marked Emad only must not appear at all; if one does, stop and ask Emad.
