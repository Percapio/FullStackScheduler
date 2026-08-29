from __future__ import annotations

from collections.abc import Callable, Iterator

from fastapi import Depends, Query, HTTPException
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..services.history_export import HISTORY_EXPORT_COLUMNS_BY_KEY, DelimiterToken


def get_session() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def get_session_factory() -> Callable[[], Session]:
    """Return the session factory used by endpoints that open their own sessions.

    This is a FastAPI dependency so tests can override it via
    app.dependency_overrides[get_session_factory] = lambda: test_session_factory.
    """
    return SessionLocal


# Contract: PageParams.limit
#   Invariant: limit <= MAX_PAGE_ROWS.
#   Rationale: per-request memory budget = MAX_PAGE_ROWS * worst-case
#              load-option fan-out (currently ~3 for Job list endpoints).
#              raising MAX_PAGE_ROWS requires re-deriving the budget below.
MAX_PAGE_ROWS: int = 500


class PageParams:
    def __init__(
        self,
        limit: int = Query(100, ge=1, le=MAX_PAGE_ROWS),
        offset: int = Query(0, ge=0),
    ):
        self.limit = limit
        self.offset = offset


class HistoryPageParams(PageParams):
    def __init__(
        self,
        limit: int = Query(50, ge=1, le=MAX_PAGE_ROWS),
        offset: int = Query(0, ge=0),
        search: str | None = Query(default=None, max_length=128),
    ):
        self.limit = limit
        self.offset = offset
        self.search = (search or "").strip() or None


class ErroredPageParams:
    def __init__(
        self,
        limit: int = Query(50, ge=1, le=MAX_PAGE_ROWS),
        offset: int = Query(0, ge=0),
        search: str | None = Query(default=None, max_length=128),
    ):
        self.limit = limit
        self.offset = offset
        self.search = (search or "").strip() or None


def get_pagination(page: PageParams = Depends()) -> PageParams:
    return page

class HistoryExportParams:
    def __init__(
        self,
        column: list[str] = Query(..., min_length=1),
        delimiter: str = Query(...),
        search: str | None = Query(default=None, max_length=128),
    ):
        for col in column:
            if col not in HISTORY_EXPORT_COLUMNS_BY_KEY:
                raise HTTPException(status_code=422, detail=f"Unknown column key: {col}")
        try:
            self.delimiter_token = DelimiterToken(delimiter)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown delimiter token: {delimiter}")
            
        self.column_keys = column
        self.search = (search or "").strip() or None
