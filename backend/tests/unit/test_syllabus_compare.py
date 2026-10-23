"""Unit tests for Step 5: Syllabus comparison, referee checks, officer review and versioned mapping creation."""
import pytest
from sqlalchemy import select

from backend.app.db.models import Application, CourseMapping, RowAction, RowState
from backend.app.db.session import app_session
from backend.app.services import mapping as mp
from backend.app.services import syllabus_compare as sc
from backend.app.services.evidence import EvidenceStore
from backend.tests.conftest import ADMIN, run_to_review


def test_aud_syllabus_missing_stops_and_does_not_invent_clos(case_env):
    """If AUD course has no outcomes/CLOs on file, comparison is marked AUD_SYLLABUS_MISSING and stopped."""
    with app_session() as db:
        app = Application(id="app-test-syl", kind="demo", status="CONFIRMED", student="Test Student", rows=[{"id": "r1", "title": "Calculus"}])
        db.add(app)
        st = RowState(app_id="app-test-syl", row_id="r1", status="Conditional", kind="unmapped")
        db.add(st)
        db.commit()

        # Target course with empty outcomes
        res = sc.compare_syllabus_for_row(db, "app-test-syl", "r1", "ACCG 201")
        assert res["status"] in ("AUD_SYLLABUS_MISSING", "TARGET_NOT_FOUND")
        assert "AUD syllabus missing" in res["reasoning"] or "not found" in res["reasoning"]
        assert len(res["matches"]) == 0


def test_referee_check_filters_invalid_citations_and_downgrades():
    """Referee checks strip invalid citations and downgrade recommendation if none remain."""
    store = EvidenceStore()
    store.add("src-doc1-e1", "src-doc1", 1, "Variables, loops and conditionals.")
    store.add("tgt-doc1-o1", "tgt-doc1", 1, "Write Python programs with control structures.")

    # 1. Valid pair survives
    raw_valid = {
        "recommendation": "accept",
        "matches": [{"source_citation": "src-doc1-e1", "target_citation": "tgt-doc1-o1", "explanation": "Direct match"}],
        "partial_matches": [],
        "flags": [],
    }
    v1 = sc.run_referee_check(raw_valid, store, "src-doc1", "tgt-doc1")
    assert v1["recommendation"] == "accept"
    assert len(v1["matches"]) == 1
    assert "NO_VALID_CITATIONS" not in v1["flags"]

    # 2. Invalid citation is stripped and suggestion downgraded
    raw_invalid = {
        "recommendation": "accept",
        "matches": [{"source_citation": "src-doc1-fake-99", "target_citation": "tgt-doc1-o1", "explanation": "Invented citation"}],
        "partial_matches": [],
        "flags": [],
    }
    v2 = sc.run_referee_check(raw_invalid, store, "src-doc1", "tgt-doc1")
    assert len(v2["matches"]) == 0
    assert any("INVALID_CITATION_REMOVED:src-doc1-fake-99" in f for f in v2["flags"])
    assert "NO_VALID_CITATIONS" in v2["flags"]
    assert v2["recommendation"] == "reject"
    assert "Downgraded from accept to reject" in v2["reasoning"]


@pytest.mark.asyncio
async def test_officer_review_accept_writes_versioned_mapping(case_env):
    """Officer accepts syllabus proposal with add_to_mapping: creates versioned CourseMapping row."""
    app_id = await run_to_review(case_env)
    with app_session() as db:
        app = db.get(Application, app_id)
        row_id = app.rows[0]["id"]
        mp.save_state(db, app_id, row_id, status=mp.CONDITIONAL, reason="Awaiting syllabus review")

        # Officer accepts with add_to_mapping=True
        res = sc.officer_review_syllabus(
            db,
            app_id=app_id,
            row_id=row_id,
            action="accept",
            add_to_mapping=True,
            aud_code="CS101",
            credits=3,
            actor="Jane Doe",
        )
        assert res["ok"] is True
        assert res["mapping_created"] is not None

        # Verify CourseMapping row exists in database
        cm = db.scalars(select(CourseMapping).where(CourseMapping.version == res["mapping_created"])).first()
        assert cm is not None
        assert cm.aud_code == "CS101"
        assert cm.aud_credits == 3
        assert cm.status == "ACTIVE"

        # Verify RowAction recorded
        action_rec = db.scalars(select(RowAction).where(RowAction.app_id == app_id, RowAction.row_id == row_id)).first()
        assert action_rec is not None
        assert action_rec.action == "CHANGE"
        assert action_rec.actor == "Jane Doe"

        # Verify row state is now Mapped
        st = mp.get_state(db, app_id, row_id)
        assert st.status == mp.MAPPED


@pytest.mark.asyncio
async def test_officer_review_reject(case_env):
    """Officer rejects syllabus proposal: row is set to No mapping."""
    app_id = await run_to_review(case_env)
    with app_session() as db:
        app = db.get(Application, app_id)
        row_id = app.rows[0]["id"]

        res = sc.officer_review_syllabus(
            db,
            app_id=app_id,
            row_id=row_id,
            action="reject",
            add_to_mapping=False,
            actor="Jane Doe",
        )
        assert res["ok"] is True
        assert res["mapping_created"] is None

        st = mp.get_state(db, app_id, row_id)
        assert st.status == mp.NO_MAPPING


@pytest.mark.asyncio
async def test_syllabus_review_api_endpoints(case_env):
    """Test API endpoint for syllabus review."""
    app_id = await run_to_review(case_env)
    with app_session() as db:
        app = db.get(Application, app_id)
        row_id = app.rows[0]["id"]

    # Officer review endpoint
    r = await case_env.client.post(
        f"/api/v1/applications/{app_id}/rows/{row_id}/syllabus-review",
        json={"action": "accept", "add_to_mapping": True, "aud_code": "MTH201", "credits": 4},
        headers=ADMIN,
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["mapping_created"] is not None
