# CreditBridge: requirements and decisions (living file)

Updated 2026-10-23 (evening). Source of truth for what we are building. Claude Code reads it as docs/REQUIREMENTS.md in the repo. Items marked OPEN need Emad's input.

## 0. Scope
- Priority curricula: AP, IB, A Level, French Bac. Plus university transfer students (for example UOWD year 1 to AUD year 2).
- Demo and real demo: AP only. Same features in the replay demo and the live AI demo, behind one working toggle.
- Programs for now: all nine plans are loaded (Computer Engineering plus eight BBA majors), but every test student is either BBA Finance or BS Computer Engineering. Demo student: Rania, BBA Finance.
- We build the real implementation. The demo only runs it on prepared data and recordings. No demo-specific code paths.
- Final say is always the officer. Agents suggest, code checks, nothing becomes credit without officer approval.
- Not authorized yet (from the build plan): sending real email to students, real AUD integration, real student data. Email is built as an outbox plus preview. Real sending stays off until Emad says so.

## 1. Demo labelling and toggle
- Each demo case is named by its transcript file: demo_ap_<student>_<variant>. Each case lists what it proves (README table: file, what you will see).
- One header toggle: Replay or Live AI. Same screens, same code paths, same case list. Replay plays a recording made from a real run. Live calls the model. Replay shows a small "Demo mode" chip.
- A recording is tied to a transcript file hash and a data version (mapping version plus plan file hashes). If data changes, the picker marks it "recording out of date" instead of playing it.
- Planted-error switches (copy error, misread) are separate named variants.

## 2. Extraction header
- One field "Institution / curriculum" at the top of the extraction. Filled from the document: a curriculum (AP, IB, A Level, French Bac) for a school transcript, a university name for a university transcript.
- Student type follows from it: school or university. This decides what is required below.
- Student name check against the case record stays.

## 3. Extraction fields
- Per course: code (if present), name, grade, credits (if shown), year/term.
- Credits: shown after extraction exactly as printed. School: if not printed, not needed and not shown. University: required. If missing, a request item is raised (back to the request pipeline).
- Major: extracted for university transfer students. Schools have none. If a university transcript shows no major, request it.
- Every field has Edit, and Re-evaluate with AI.

### Re-evaluation design (decision)
- Default: re-evaluate only the requested field. The agent re-reads that cell (crop, higher zoom) without being shown the old value, so it is not anchored. Officer sees old and new side by side and picks one.
- Code then recomputes everything that depends on that field: checks (totals, grade scale), eligibility (minimum grade), mapping lookup, classification, credit totals. This is deterministic and automatic. No model call.
- Escalation options in a menu: "Re-read this row" and "Re-read this page". Never an automatic whole-document re-run.
- Officer edits are never overwritten by AI. Edited fields are marked "edited by officer" with who and when. Re-evaluating an edited field asks first.
- Re-evaluation uses the same budget and ledger as any agent call.

## 4. Mapping data (DONE for AP)
- File: policies/data/mapping_ap.csv, converted from AUD's AP sheet (43 rows after clean-up). Columns: source_course, aud_code, aud_name, aud_credits, min_exam_score, notes, version (2026-10), status, detail_id.
- Status values: active (code and credits 1+), no_credit (valid code, 0 credits: shown as "Mapped, no credit awarded"), needs_review (odd code such as MATH104/105 or ELEC for ARCH, or blank credits: never auto-placed, goes to the officer), no_equivalent (Not applicable with reason).
- Decisions: Calculus AB maps to MATH 200 (business core) and MATH 210/205 (engineering, 0 credits). Statistics and Microeconomics use the 3-credit rows; duplicate rows dropped. Source names normalised to how transcripts write them.
- min_exam_score is blank in the sheet. AP Statistics needing a 4 or higher comes from policy, not this file.
- Mapped rows need no model.
- Course not in the mapping (a new course): flag as No mapping and raise a request item (syllabus). See section 8. The officer can later add the approved result to the mapping table (proposal, officer approves).
- Load-time validation refuses duplicate (source_course, aud_code) rows and unknown status values. Credit disagreement with the catalog (CHEM 200: 3 in sheet, 4 in catalog) is a warning.

## 5. Eligibility: minimum grade (hard rule)
- School transcripts: minimum grade C. Anything below C is NOT transferred. It is not a candidate for any placement (core, elective category or general elective). It is listed in the Not applicable section with the reason "Grade below C, not eligible for transfer credit".
- No Include anyway on a below-C row. The officer approves the outcome like any other row.
- Grading scales differ. Each curriculum needs a conversion table to "C equivalent". OPEN: AP (letter and exam score), A Level (A* to E), IB (1 to 7), French Bac (/20). Do AP first.
- University transfers: minimum grade rule OPEN (assume C until told).
- Edge cases: pass/fail grades, missing grade (request or flag, never assume), predicted grades, in-progress courses, repeated courses (use latest or best: OPEN), withdrawn courses (ignore), grade printed as number only.

## 6. Program plans and general education (The Big Questions)
Data (parsed once to YAML, loaded by code): policies/data/gen_ed_categories.yaml and policies/data/programs/*.yaml (computer_engineering, accounting, ai_in_business, aimc, economics, finance, general, management, marketing).

### 6.1 The Big Questions (catalog 2026-2027): 18 credits
- 3 credits from each of five categories, plus one elective course from a BQ category of the student's choice (slot BQ-ELE).
- Categories: BQ-TECH, BQ-IMAG, BQ-CULT, BQ-ETHI, BQ-SCIE. Each has a list of eligible AUD courses.
- A course counts once. Multi-category courses (PHIL 225 in TECH and ETHI, PHIL 250 and WLDC 301 in CULT and ETHI): fill the category with fewer options first, officer can swap.
- Exclusions (credit for only one): SCIE 201 with BIOL 201 or BIOL 202, CHEM 200 with CHEM 201, EECE 200 with EECE 201.
- Credits for a placed course come from the mapping row. A 4-credit course (PHYS 201) in a 3-credit slot: placed credits are shown as mapped; the plan total is a display figure. OPEN: whether the extra credit counts toward the degree.
- ENGL 275-78 means ENGL 275 to 278. SSCI 275/475 and SCIE 275 are special topics: candidate, flagged for a request.
- HUMN 398 The Core Experience is a core course, not a BQ course.

### 6.2 Degree plans (done)
- Plan items: core courses, slots (BQ-*, program electives, GEN-ELE), starred courses that already fill a BQ category (Computer Engineering: ENGG 140 TECH, BIOL 201 SCIE, PHIL 222 ETHI; AI in Business: BUAI 140 TECH).
- A slot_id can repeat (ACCG-ELE x3, ECON-ELE x4, MAJOR-ELE x9 ...): each is a separate instance.
- The business plans have no BQ-TECH slot. A core course that is on a BQ category's list fills that category (COMP 101 for BQ-TECH).
- Remedial block (ENGL 100, MATH 095, MATH 103/104, remedial COMP 101) is not part of the degree and never core. The remedial COMP 101 (Introduction to Computers) is not the BQ-TECH COMP 101.
- "suggested" on a slot is a label, never auto-picked: GEN-ELE of Computer Engineering suggests BUSI 101; BQ-ETHI of Finance and AI in Business suggests PSYC 201.
- Printed credit totals differ from computed totals in every plan (source issue). Keep both, do not block loading.

### 6.3 Placement of an incoming course (code, from the mapped AUD course)
Only courses at grade C or above reach this step. Order:
1. AUD course is a core course in the student's plan: Core.
2. Unfilled BQ category slot it belongs to: candidate for that category.
3. Category already filled: candidate for BQ-ELE (once).
4. Program elective slot it is eligible for: candidate. OPEN: elective lists per program.
5. Otherwise the one GEN-ELE, if the area is not blocked for the program.
- A mapped course that is not in the student's plan (core, slot, BQ list) is a GEN-ELE candidate only. This replaces the earlier idea of blocking calculus for business: MATH 200 is business core, MATH 210 is Computer Engineering core.
- Blocked areas (program_constraints.yaml) now apply only to GEN-ELE candidates. For engineering: ACCG, MGMT, MKTG, FINA, BUAI blocked; BUSI is not blocked (BUSI 101 is the usual general elective, shown as a suggestion).
- Where two courses compete for one slot (Chemistry and Physics for BQ-SCIE), code lists valid placements and the Case agent proposes the assignment with a reason; code checks it; officer approves.
- Limits: one BQ-ELE, one GEN-ELE (3 credits) per student. Total approved transfer credits are counted and shown (running total on screen and in the output).
- When several courses are eligible for one slot, all of them stay as candidates and the officer picks which one goes to the final table. The ones not picked drop to the next option, and can still be the single GEN-ELE candidate.
- A course is a GEN-ELE candidate only when no category or program slot is left for it. A non-degree course is not automatically a general elective.
- Worked example (engineering student with AP Psychology, AP Sociology, AP Dance, AP Economics): the social science requirement (a BQ category) can be filled by AP Psychology or AP Sociology; the officer chooses. The other one, AP Dance (no matching AUD course) and AP Economics (the AUD economics course is not in the engineering program, so it cannot map there) are candidates for the one GEN-ELE. Only one of them can be approved.
- Edge cases: category filled by an earlier choice, officer swap (totals update), slot with no candidate shown as "not met", partial credits, duplicate coverage (AP Calc AB and BC to one course: one counts, the other "already covered"), exclusion groups.

## 7. University transfer students
- Different path from school transcripts. Credits required. Major required.
- No mapping database for other universities: every course is a new-course case. Requests are guaranteed (syllabus or course outline with learning outcomes, credits, official transcript).
- Matching: compare the course outline against the AUD course syllabus and CLOs (section 9).
- Edge cases: different credit systems (credits vs ECTS vs hours: conversion OPEN), courses older than N years (OPEN), grade scale of the other university, several universities on one transcript, institution not recognised, transcript in Arabic or French.

## 8. Request and email pipeline
- A request item is the unit: course, what is needed, why (one line), status (pending, received). Raised by code or by the Case agent.
- One email per student, built from all open items grouped together. Items: syllabus or outline, exam score report, official transcript with credits, translation, other.
- Officer controls: add item (from templates or free text), remove item, edit the text, preview, send. Items she adds are tracked like any other item.
- Email structure: greeting with student name, one sentence on what the review needs, a checklist (course name and one line of why each), how to send, deadline line, signature. Short.
- Sending in demo and now: writes to an outbox and shows the preview. The Send button is labelled "Simulated send". Real SMTP is off by default and needs Emad's explicit go-ahead.
- Replies: upload tied to the case marks the item received, and the Case agent resumes only the affected row(s).
- Edge cases: wrong file, partial replies, reminders, duplicate requests, officer cancels an item, student email missing.

## 9. Syllabus comparison
- Case agent reads the new syllabus and the AUD syllabus and CLOs for the target course.
- It lists matches, partial matches and gaps, citing both sides, then gives a suggestion (accept, partial, reject) with reasoning. No invented percentages.
- Officer accepts or rejects. The agent cannot set Mapped.
- Accepting offers "Add to mapping table" (officer approves, versioned).
- Edge cases: unreadable or scanned syllabus (Intake re-reads), wrong course, syllabus in another language, AUD syllabus missing.

## 10. Phases
1. Now (AP only): (a) real data and placement step: data files, validation, placement, credit totals, students limited to Finance and Computer Engineering, Case agent slot contention, Intake speed fix, toggle and recordings. (b) Extraction header, credits display, field edit and re-evaluate (sections 2 and 3). Step (a) is being handed to Claude Code first; step (b) follows.
2. Next: request items and the email outbox, syllabus comparison, add-to-mapping proposal.
3. Later: IB, A Level, French Bac (grade tables), university transfer path with required credits and major.

## 11. Open items (Emad)
- Program elective lists (for example CompE ELE options, FINA ELE options).
- Whether the extra credit of a 4-credit course in a 3-credit slot counts.
- Which catalog version the plan sheets bind to (2025 curriculum against 2026-2027 catalog).
- Grade conversion tables for IB, A Level, French Bac. University transfer minimum grade and credit conversion.
- Repeated courses and age-of-course rules.
- Whether real email sending is ever allowed in the demo.
- needs_review AP rows (odd codes, blank credits): confirm with admissions when real data is available.

