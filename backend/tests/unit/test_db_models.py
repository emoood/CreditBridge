from sqlalchemy import create_engine, inspect

from backend.app.db.models import Base

EXPECTED = {"applications", "jobs", "job_units", "decisions", "audit_events", "course_mappings", "row_states", "trace_events", "usage_ledger", "row_actions", "model_log", "outbox"}


def test_schema_creates_on_sqlite_and_has_expected_tables():
    e = create_engine("sqlite://")
    Base.metadata.create_all(e)
    assert set(inspect(e).get_table_names()) == EXPECTED


def test_no_approval_column_outside_decisions():
    for t in Base.metadata.tables.values():
        if t.name != "decisions":
            assert not any("approv" in c.name.lower() for c in t.columns), t.name


def test_no_curriculum_tables_defined_here():
    assert all(t.schema is None for t in Base.metadata.tables.values())
    assert not any("curriculum" in t.name for t in Base.metadata.tables.values())
