from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        url = get_settings().database_url
        connect_args = {}
        if url.startswith("postgresql"):
            # Never hang forever on a lock or a runaway query.
            connect_args = {"options": "-c lock_timeout=10000 -c statement_timeout=60000"}
        _engine = create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10, future=True,
                                connect_args=connect_args)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def reset_engine() -> None:
    """Used by tests after changing DATABASE_URL."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
