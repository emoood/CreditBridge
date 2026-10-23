Read docs/OWNERSHIP.md and docs/REQUIREMENTS.md (sections 8 and 9) first. Chunk C2. C0 is merged. Follow the Git, Rules and Change log sections in OWNERSHIP.md. Only edit the files OWNERSHIP.md allows for C2.
Audit first (a few lines), then build in order, one commit per step. Labels: [CODE] deterministic, [AGENT] model-driven with code verifying.

What already exists (verify, do not rebuild):

- RowState.requests (backend/app/db/models.py): per-row list of {id, what, why, to, status: DRAFT|SENT|RECEIVED}, created by the Case agent in backend/app/agents/tools/case_tools.py. This is the single request store. Ignore the older repo.syllabus_requests lists in matching_agent.py, orchestrator.py and evidence_tools.py; if the two disagree, say so in the log.
- The student's email and phone are in the student record (student_record.load_record). StudentPopup.tsx lists a student's open requests.
- A one-row resume path exists for "Waiting on document" rows. Find it and reuse it.
- Evidence tools and the citation referee checks (orchestrator.py, tools/evidence_tools.py) exist; reuse them for step 5.

Step 1. [CODE] Request items
Request item stays as it is. Add the type to what is needed: syllabus or outline, exam score report, official transcript with credits, translation, other. Do not change who raises them.

Step 2. [CODE + UI] One email per student
Built from all open items across that student's rows, grouped. Structure: greeting with student name, one sentence on what the review needs, a checklist (course name plus one line of why each), how to send (reply or an upload link tied to the case), deadline line, signature. Short. Officer controls: add an item (templates or free text), remove, edit the text, preview, send. Added items are tracked like any other.

Step 3. Real sending
The officer clicks Send and the email goes out by SMTP from the project mailbox. Credentials come from .env only (SMTP host, port, user, password, sender address); add them to .env.example with empty values and never commit real ones. Safeguards:

- Config switch EMAIL_SENDING, off by default. When off, Send behaves as "Simulated send" (outbox and preview only).
- Recipient allowlist in config. Never send to an address that is not on it.
- The invented demo students use example.invalid addresses. In demo and test, redirect their mail to a test inbox (config value) and put a line in the email body saying who it would have gone to.
- Every send is written to the Outbox table with time, recipient, items and result (sent or error). A failed send keeps the items pending and shows the error.
- No agent can send. Only the officer's Send click calls the sender.
- Unit tests use a fake sender; no test opens a real connection.

Step 4. [CODE] Replies
An upload tied to the case (one-time token link) marks the item RECEIVED and resumes only the affected row(s), through the existing one-row resume path. Edge cases: student email missing, duplicate requests for one course, officer cancels an item, partial reply, wrong file, reminders.

Step 5. [AGENT] Syllabus comparison (when a requested syllabus is RECEIVED)

- The Case agent reads the student's new syllabus and the AUD course's syllabus and CLOs for the target course, and lists matches, partial matches and gaps, citing both sides, then gives a suggestion (accept, partial, reject) with reasoning. No invented percentages.
- Code checks: every citation resolves to a stored excerpt on the right side (reuse the referee checks); invalid citations are removed; none left means the suggestion is downgraded.
- The officer accepts or rejects. The agent cannot set Mapped and cannot approve.
- Accepting offers "Add to mapping table": officer approves, a versioned row is written to course_mappings (insert through a function in your new files; do not edit mapping.py).
- Audit first what AUD syllabus/CLO data exists in the repo. The catalog (catalog/aud_undergrad_2026_2027) may hold descriptions only. If the AUD syllabus or CLOs are missing for a course, mark the comparison "AUD syllabus missing" and stop there; do not invent CLOs.
- Edge cases: unreadable or scanned syllabus (send back for re-read), wrong course, syllabus in another language, AUD syllabus missing.

Step 6. Tests (small)
One email per student from several items; add, remove and edit an item; simulated send writes the outbox and sends nothing; recipient not on the allowlist is refused; a reply resumes only its row; syllabus comparison with an invalid citation is downgraded; accept writes a versioned mapping row only after approval.

Wording in the UI and emails: plain, short, no "need you", no "stale".
Finish: change log docs/changes/C2.md, a line in INDEX.md, C2 row status, push to main. Final report under 15 lines: audit result, what was built, what was not, and any file outside the allowed list you had to touch and why.
