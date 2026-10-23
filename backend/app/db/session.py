"""Two separate databases:
 - the CURRICULUM database (DATABASE_URL, Supabase): read-only, owned by a teammate; `get_engine`.
 - this app's OWN state (APP_DATABASE_URL, local PostgreSQL): jobs, units, applications, decisions, audit; `get_app_engine`."""
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings


@lru_cache
def get_engine():
    url = get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2, connect_args={"connect_timeout": 10})


_app_engine: Engine | None = None


def get_app_engine() -> Engine:
    global _app_engine
    if _app_engine is None:
        _app_engine = create_engine(get_settings().app_database_url, pool_pre_ping=True)
    return _app_engine


def set_app_engine(engine: Engine | None) -> None:
    """Tests inject an in-memory engine."""
    global _app_engine
    _app_engine = engine


def app_session() -> Session:
    return sessionmaker(get_app_engine(), expire_on_commit=False)()


def init_app_db() -> None:
    from sqlalchemy import inspect, text

    from .models import Base
    engine = get_app_engine()
    Base.metadata.create_all(engine)
    if "programs" not in {c["name"] for c in inspect(engine).get_columns("course_mappings")}:  # a table made before mappings could be limited to a program
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE course_mappings ADD COLUMN programs JSON"))
