"""Database engines and sessions.

Why: three roles exist (docs/01 §11). The worker, bot and news write with `saley`. The
web-api reads with saley_ro so a bug in a view can never write. Migrations use saley_admin.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from saley.core.settings import get_settings


class Base(DeclarativeBase):
    """Declarative base for all Saley models."""


_ENGINES: dict[str, Engine] = {}


def _engine(url: str) -> Engine:
    """One pooled engine per URL per process. Sessions speak IST, so times come back in IST."""
    if url not in _ENGINES:
        _ENGINES[url] = create_engine(
            url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=10,
            pool_timeout=5,
            connect_args={
                "options": f"-c timezone={get_settings().saley_tz}",
                "connect_timeout": 5,
            },
        )
    return _ENGINES[url]


def rw_engine() -> Engine:
    return _engine(get_settings().database_url)


def ro_engine() -> Engine:
    return _engine(get_settings().database_url_ro)


def admin_engine() -> Engine:
    return _engine(get_settings().admin_database_url)


def dispose_engines() -> None:
    """Close every pool. Tests call this after they switch the database URL."""
    for engine in _ENGINES.values():
        engine.dispose()
    _ENGINES.clear()


def session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=engine or rw_engine(), expire_on_commit=False)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    """One unit of work: commit on success, roll back and re-raise on error."""
    session = session_factory(engine)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
