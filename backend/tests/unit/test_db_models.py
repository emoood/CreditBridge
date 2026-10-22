from sqlalchemy import create_engine, inspect

from backend.app.db.models import Base

EXPECTED = {"policy_versions", "applications", "documents", "document_excerpts", "source_courses",
            "source_course_corrections", "target_courses", "job_runs", "match_runs", "match_candidates",
            "match_evidence", "rule_results", "agent_reviews", "human_decisions", "upload_requests",
            "audit_events", "llm_events"}


def test_schema_creates_on_sqlite_and_has_expected_tables():
    e = create_engine("sqlite://")
    Base.metadata.create_all(e)
    assert set(inspect(e).get_table_names()) == EXPECTED


def test_no_approval_column_outside_human_decisions():
    for t in Base.metadata.tables.values():
        if t.name != "human_decisions":
            assert not any("approv" in c.name.lower() for c in t.columns), t.name


def test_no_curriculum_tables_defined_here():
    assert all(t.schema in (None, "public") for t in Base.metadata.tables.values())
    assert not any("curriculum" in t.name for t in Base.metadata.tables.values())
