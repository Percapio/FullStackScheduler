from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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
        kwargs["pool_size"] = 4
        kwargs["max_overflow"] = 0

    engine = create_engine(url, **kwargs)

    if is_sqlite and settings.sqlite_pragmas:
        event.listen(engine, "connect", _apply_sqlite_pragmas)

    return engine


def _make_session() -> Session:
    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )()


class _LazySessionLocal:
    def __call__(self) -> Session:
        return _make_session()


SessionLocal = _LazySessionLocal()
