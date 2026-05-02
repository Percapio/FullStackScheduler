from __future__ import annotations

import re
from datetime import date

from .models import JobStatus

_SHIP_TEXT_RE = re.compile(r"^\s*(\d{1,2})/(\d{1,2})")


def resolve_ship_date(
    *,
    ship_date_text: str | None,
    status: JobStatus,
    shipped_at: date | None,
    today: date,
) -> date | None:
    if not ship_date_text:
        return None
    m = _SHIP_TEXT_RE.match(ship_date_text)
    if m is None:
        return None
    month, day = int(m.group(1)), int(m.group(2))

    if status is JobStatus.shipped:
        year = shipped_at.year if shipped_at is not None else today.year
    else:
        year = today.year

    try:
        return date(year, month, day)
    except ValueError:
        return None
