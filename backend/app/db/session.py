"""Two separate databases:
 - the CURRICULUM database (DATABASE_URL, Supabase): read-only, owned by a teammate; `get_engine`.
 - this app's OWN state (APP_DATABASE_URL, local PostgreSQL or SQLite): jobs, units, applications, decisions, audit; `get_app_engine`."""
from functools import lru_cache
from pathlib import Path

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
        url = get_settings().app_database_url
        if url.startswith("sqlite"):
            Path("data").mkdir(parents=True, exist_ok=True)
            _app_engine = create_engine(url, connect_args={"check_same_thread": False})
        else:
            try:
                eng = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 2})
                with eng.connect():
                    pass
                _app_engine = eng
            except Exception:
                Path("data").mkdir(parents=True, exist_ok=True)
                _app_engine = create_engine("sqlite:///data/creditbridge.db", connect_args={"check_same_thread": False})
    return _app_engine


def set_app_engine(engine: Engine | None) -> None:
    """Tests inject an in-memory engine."""
    global _app_engine
    _app_engine = engine


def app_session() -> Session:
    return sessionmaker(get_app_engine(), expire_on_commit=False)()


def init_app_db() -> None:
    from .models import Base
    Base.metadata.create_all(get_app_engine())
