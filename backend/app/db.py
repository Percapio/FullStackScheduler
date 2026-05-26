from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings


def _apply_sqlite_pragmas(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _ensure_sqlite_dir(url: str) -> None:
    parsed = make_url(url)
    if not parsed.drivername.startswith("sqlite"):
        return
    db = parsed.database
    if not db or db == ":memory:":
        return
    Path(db).parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    url = settings.database_url
    is_sqlite = make_url(url).drivername.startswith("sqlite")

    kwargs: dict = {}
    if is_sqlite:
        _ensure_sqlite_dir(url)
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["pool_size"] = settings.sqlite_pool_size
        kwargs["max_overflow"] = settings.sqlite_max_overflow
        kwargs["pool_timeout"] = settings.sqlite_pool_timeout_seconds

    engine = create_engine(url, **kwargs)

    if is_sqlite and settings.sqlite_pragmas:
        event.listen(engine, "connect", _apply_sqlite_pragmas)

    return engine


@lru_cache(maxsize=1)
def get_session_factory() -> Callable[[], Session]:
    """
    Contract: get_session_factory
      Intent: a single sessionmaker bound to the singleton engine. The factory
              itself is invariant for the process lifetime.
      Pre:    none.
      Post:   returns the same Callable[[], Session] on every call.
      Raises: propagates engine creation errors on first call.
    """
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def SessionLocal() -> Session:
    return get_session_factory()()


@contextmanager
def scoped_write_session() -> Iterator[Session]:
    """
    Contract: scoped_write_session
      Intent: a session for code paths that mutate but do not depend on reading
              ORM instance attributes between a commit and the session's close.
      Pre:    no caller of this function reads any mapped attribute on any
              persistent instance after commit() returns on this session.
      Post:   on context exit, session is committed-or-rolled-back and closed.
              identity map is dropped on each commit.
      Raises: propagates underlying DB errors; rolls back on any exception.
    """
    session = sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=True,
    )()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
