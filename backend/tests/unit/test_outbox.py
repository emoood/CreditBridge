"""Unit tests for Chunk C2: request items, email builder, outbox safeguards and student replies."""
import pymupdf
import pytest
from sqlalchemy import select

from backend.app.db.models import Application, Job, RowState
from backend.app.db.session import app_session
from backend.app.services import mapping as mp
from backend.app.services import outbox as ob
from backend.tests.conftest import ADMIN, run_to_review


def _valid_pdf_bytes() -> bytes:
    doc = pymupdf.open()
    doc.new_page()
    return doc.tobytes()


def test_classify_and_normalize_request_items():
    assert ob.classify_request_type("Course syllabus and topics") == "syllabus or outline"
    assert ob.classify_request_type("Official AP score report") == "exam score report"
    assert ob.classify_request_type("Official transcript with credits") == "official transcript with credits"
    assert ob.classify_request_type("Certified translation into English") == "translation"
    assert ob.classify_request_type("High school graduation certificate") == "other"
    assert ob.classify_request_type("Random item", explicit_type="translation") == "translation"

    norm = ob.normalize_item({"what": "Need syllabus", "why": "Audit"})
    assert norm["type"] == "syllabus or outline"
    assert norm["status"] == "DRAFT"
    assert "token" in norm


@pytest.mark.asyncio
async def test_add_remove_edit_request_items(case_env):
    app_id = await run_to_review(case_env)
    with app_session() as db:
        app = db.get(Application, app_id)
        row_id = app.rows[0]["id"]

        # 1. Add item
        item = ob.add_request_item(db, app_id, row_id, "Official College Board score report", "Verify AP grade", "exam score report")
        assert item["type"] == "exam score report"
        assert item["status"] == "DRAFT"
        req_id = item["id"]

        # 2. Duplicate is prevented
        dup = ob.add_request_item(db, app_id, row_id, "Official College Board score report", "Different reason")
        assert dup["id"] == req_id

        # 3. Edit item
        updated = ob.edit_request_item(db, app_id, row_id, req_id, what="Updated College Board report", why="Updated reason", req_type="other")
        assert updated is not None
        assert updated["what"] == "Updated College Board report"
        assert updated["why"] == "Updated reason"
        assert updated["type"] == "other"

        # 4. Remove item
        removed = ob.remove_request_item(db, app_id, row_id, req_id)
        assert removed is True
        st = mp.get_state(db, app_id, row_id)
        assert not any(r["id"] == req_id for r in (st.requests or []))


def test_one_email_per_student_structure():
    app = Application(id="app-1", student="Sarah Connor", rows=[{"id": "r1", "title": "Calculus I"}])
    rec = {"name": "Sarah Connor", "email": "sarah@example.invalid"}
    items = [
        {"id": "1", "course_title": "Calculus I", "what": "Syllabus", "why": "Need learning outcomes", "status": "DRAFT", "token": "tok123"},
        {"id": "2", "course_title": "Physics I", "what": "AP score report", "why": "Verify grade", "status": "DRAFT", "token": "tok456"},
    ]
    content = ob.build_email_content(app, rec, items, upload_base_url="https://aud.edu")
    body = content["body"]

    # Requirements checks:
    # Plain and short, greeting with student name, one sentence on what review needs,
    # checklist (course name plus why), how to send (upload link / reply), deadline line, signature.
    # Never "need you", never "stale"
    assert "need you" not in body.lower()
    assert "stale" not in body.lower()
    assert "Dear Sarah Connor," in body
    assert "To evaluate your transfer credit for American University in Dubai, we need additional documentation" in body
    assert "• Calculus I: Syllabus (Need learning outcomes)" in body
    assert "• Physics I: AP score report (Verify grade)" in body
    assert "https://aud.edu/upload?token=tok123" in body
    assert "within 10 business days" in body
    assert "Office of Admissions\nAmerican University in Dubai" in body


@pytest.mark.asyncio
async def test_simulated_send_writes_outbox_and_sends_nothing(case_env, monkeypatch):
    monkeypatch.setenv("EMAIL_SENDING", "false")
    sent_calls = []
    ob.set_smtp_sender(lambda frm, to, sub, bdy: sent_calls.append((frm, to, sub, bdy)))

    app_id = await run_to_review(case_env)
    with app_session() as db:
        app = db.get(Application, app_id)
        row_id = app.rows[0]["id"]
        ob.add_request_item(db, app_id, row_id, "Course syllabus", "Missing outcomes", "syllabus or outline")

        entry = ob.send_student_email(db, app_id, recipient_override="student@example.org")
        assert entry.status == "SIMULATED"
        assert entry.simulated is True
        assert len(sent_calls) == 0  # No SMTP call made

        # Requests marked SENT
        st = mp.get_state(db, app_id, row_id)
        assert any(r["status"] == "SENT" for r in st.requests)

    ob.set_smtp_sender(None)


@pytest.mark.asyncio
async def test_recipient_not_on_allowlist_is_refused(case_env, monkeypatch):
    monkeypatch.setenv("EMAIL_SENDING", "true")
    monkeypatch.setenv("EMAIL_ALLOWLIST", "allowed@aud.edu")

    app_id = await run_to_review(case_env)
    with app_session() as db:
        app = db.get(Application, app_id)
        row_id = app.rows[0]["id"]
        ob.add_request_item(db, app_id, row_id, "Course syllabus", "Missing outcomes", "syllabus or outline")

        with pytest.raises(PermissionError, match="not on the email allowlist"):
            ob.send_student_email(db, app_id, recipient_override="unauthorized@external.com")


@pytest.mark.asyncio
async def test_demo_student_redirect_to_test_inbox(case_env, monkeypatch):
    monkeypatch.setenv("EMAIL_SENDING", "true")
    monkeypatch.setenv("EMAIL_TEST_INBOX", "testinbox@aud.edu")
    monkeypatch.setenv("EMAIL_ALLOWLIST", "testinbox@aud.edu")

    sent_calls = []
    ob.set_smtp_sender(lambda frm, to, sub, bdy: sent_calls.append((frm, to, sub, bdy)))

    app_id = await run_to_review(case_env)
    with app_session() as db:
        app = db.get(Application, app_id)
        row_id = app.rows[0]["id"]
        ob.add_request_item(db, app_id, row_id, "Transcript", "Missing credits", "official transcript with credits")

        entry = ob.send_student_email(db, app_id, recipient_override="student123@example.invalid")
        assert entry.status == "SENT"
        assert entry.recipient == "testinbox@aud.edu"
        assert entry.original_recipient == "student123@example.invalid"
        assert len(sent_calls) == 1
        _, recipient, _, body = sent_calls[0]
        assert recipient == "testinbox@aud.edu"
        assert "[Original Recipient: student123@example.invalid]" in body

    ob.set_smtp_sender(None)


@pytest.mark.asyncio
async def test_reply_upload_resumes_only_its_row(case_env):
    app_id = await run_to_review(case_env)
    with app_session() as db:
        app = db.get(Application, app_id)
        target_row_id = app.rows[0]["id"]
        other_row_id = app.rows[1]["id"]

        # Add requests to both rows
        it1 = ob.add_request_item(db, app_id, target_row_id, "Calculus syllabus", "Check outcomes", "syllabus or outline")
        it2 = ob.add_request_item(db, app_id, other_row_id, "Physics exam report", "Check score", "exam score report")
        token1 = it1["token"]

        # Student uploads reply for item 1
        res = ob.handle_reply_upload(db, token=token1, filename="calc_syllabus.pdf", content=_valid_pdf_bytes(), note="Here is the syllabus")
        assert res["ok"] is True
        assert res["row_id"] == target_row_id

        # Verify target row is updated and request item 1 marked RECEIVED
        st1 = mp.get_state(db, app_id, target_row_id)
        req1 = next(r for r in st1.requests if r["id"] == it1["id"])
        assert req1["status"] == "RECEIVED"
        assert req1["document"]["filename"] == "calc_syllabus.pdf"
        assert st1.facts.get("syllabus") is True

        # Verify other row is NOT marked received
        st2 = mp.get_state(db, app_id, other_row_id)
        req2 = next(r for r in st2.requests if r["id"] == it2["id"])
        assert req2["status"] == "DRAFT"

        # Verify Job was queued specifically for target_row_id
        job = db.get(Job, res["job_id"])
        assert job is not None
        assert job.phase == "CASE"
        assert job.summary.get("row_id") == target_row_id


@pytest.mark.asyncio
async def test_outbox_api_routes(case_env):
    app_id = await run_to_review(case_env)
    with app_session() as db:
        app = db.get(Application, app_id)
        row_id = app.rows[0]["id"]

    # 1. Add request via API
    r_add = await case_env.client.post(
        f"/api/v1/applications/{app_id}/rows/{row_id}/requests",
        json={"what": "Syllabus with CLOs", "why": "Content check", "type": "syllabus or outline"},
        headers=ADMIN,
    )
    assert r_add.status_code == 200
    item = r_add.json()
    req_id = item["id"]
    token = item["token"]

    # Non-officer cannot add request
    r_forbid = await case_env.client.post(
        f"/api/v1/applications/{app_id}/rows/{row_id}/requests",
        json={"what": "Test", "why": "Test"},
        headers={"X-Role": "student"},
    )
    assert r_forbid.status_code == 403

    # 2. Preview email
    r_prev = await case_env.client.get(f"/api/v1/applications/{app_id}/outbox/preview")
    assert r_prev.status_code == 200
    prev_data = r_prev.json()
    assert "Syllabus with CLOs" in prev_data["body"]

    # 3. Check reply token
    r_tok = await case_env.client.get(f"/api/v1/reply/{token}")
    assert r_tok.status_code == 200
    assert r_tok.json()["valid"] is True

    # 4. Student reply upload
    r_reply = await case_env.client.post(
        "/api/v1/reply",
        data={"token": token, "note": "Attaching requested outline"},
        files={"file": ("outline.pdf", _valid_pdf_bytes(), "application/pdf")},
    )
    assert r_reply.status_code == 200
    assert r_reply.json()["ok"] is True
