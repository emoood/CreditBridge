# Agent architecture

Agents are investigators; code is the referee. Neither agent can approve credit.

## Flow per subject (`agents/orchestrator.py: run_case`)
1. **MatchingAgent** investigates with tools and submits one of: `POTENTIAL_MATCH`, `POTENTIAL_GENERAL_ELECTIVE` (flag only), `NO_DEFENSIBLE_MATCH`, `NEEDS_MORE_EVIDENCE`.
2. **Rules** (pure Python) run on any claimed course.
3. **ReviewerAgent** tries to refute the claim from primary evidence (never sees the matcher's explanation).
4. If `CHALLENGED`: the reviewer is **advisory only**. Its objections (with excerpt ids) and minor remarks are shown next to the matcher's pick; nothing is fed back to the matcher and the first pick always stands. Triage: `NEEDS_ATTENTION`.
5. **Triage** (pure Python) assigns the officer's worklist category.
6. The admissions officer accepts or declines. Only a human decision is final.

## Shared pieces
| Piece | File | Role |
|---|---|---|
| Case file | `agents/case_file.py` | Working record: queries, what was read, notes, rule checks, precedents. Shown to the officer. Not chain-of-thought. |
| Precedent memory | `agents/precedent.py` | Past officer decisions, searchable. Empty until decisions/imports arrive. |
| Tools (11) | `agents/tools/evidence_tools.py` | Read-only, role-scoped. Only writes: case notes, in-app syllabus request draft. |
| Loop | `agents/loop.py` | Bounded tool rounds (note-only turns are free), `submit_result` finish, one repair attempt. |
| Triage | `services/triage.py` | READY_TO_ACCEPT, NEEDS_ATTENTION, CONTESTED, BLOCKED_BY_RULE, NEEDS_EVIDENCE, POTENTIAL_GE, NO_MATCH. |

## Referee checks (code, after the model)
- Claimed course must have been read by the matcher (`TARGET_NEVER_READ`).
- Every citation must resolve to a stored excerpt in the right course; invalid ones are removed; none left => downgrade.
- No syllabus => `NEEDS_SYLLABUS` plus an in-app request, whatever the model said.
- General elective result clears course and citations.
- Output schemas have no approval field. Rule results cannot be changed by agents.

## Known behavior to tune (first live runs, qwen3.5:4b, single runs)
- Reviewer can challenge on pedantic grounds; the matcher then over-withdraws a good match (COMP101 ended as `NO_DEFENSIBLE_MATCH`).
- One run exhausted the tool budget without submitting (INFS230).
- Precedent is empty, so that tool returns nothing yet.
