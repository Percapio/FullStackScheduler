from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from ..db import SessionLocal


def get_session() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


class PageParams:
    def __init__(
        self,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        self.limit = limit
        self.offset = offset


class HistoryPageParams(PageParams):
    def __init__(
        self,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        search: str | None = Query(default=None, max_length=128),
    ):
        self.limit = limit
        self.offset = offset
        self.search = (search or "").strip() or None


class ErroredPageParams:
    def __init__(
        self,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        search: str | None = Query(default=None, max_length=128),
    ):
        self.limit = limit
        self.offset = offset
        self.search = (search or "").strip() or None


def get_pagination(page: PageParams = Depends()) -> PageParams:
    return page
