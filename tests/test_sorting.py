from datetime import date

import pytest

from backend.app.models import JobStatus
from backend.app.sorting import resolve_ship_date


class TestResolveShipDate:
    def test_non_shipped_uses_today_year(self):
        result = resolve_ship_date(
            ship_date_text="9/15",
            status=JobStatus.planned,
            shipped_at=None,
            today=date(2026, 4, 19),
        )
        assert result == date(2026, 9, 15)

    def test_shipped_uses_shipped_year(self):
        result = resolve_ship_date(
            ship_date_text="9/15",
            status=JobStatus.shipped,
            shipped_at=date(2025, 9, 15),
            today=date(2026, 4, 19),
        )
        assert result == date(2025, 9, 15)

    def test_shipped_no_shipped_at_fallback_to_today(self):
        result = resolve_ship_date(
            ship_date_text="9/15",
            status=JobStatus.shipped,
            shipped_at=None,
            today=date(2026, 4, 19),
        )
        assert result == date(2026, 9, 15)

    @pytest.mark.parametrize(
        "text",
        ["TBD", None, ""],
    )
    def test_unparseable_text_returns_none(self, text):
        result = resolve_ship_date(
            ship_date_text=text,
            status=JobStatus.planned,
            shipped_at=None,
            today=date(2026, 4, 19),
        )
        assert result is None

    def test_invalid_date_returns_none(self):
        result = resolve_ship_date(
            ship_date_text="2/30",
            status=JobStatus.planned,
            shipped_at=None,
            today=date(2026, 4, 19),
        )
        assert result is None
