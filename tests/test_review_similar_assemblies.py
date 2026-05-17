"""Tests for _similar_assemblies_compute — P-3 algorithmic fixes.

Covers:
- Length-bucket filter: assembly whose length differs by >LENGTH_WINDOW is excluded.
- Single Levenshtein invocation per candidate (no doubled calls).
- Top-N ordering: ascending edit distance.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, call

from backend.app.models import Assembly
from backend.app.api.ingest import _similar_assemblies_compute


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def session_with_assemblies(session):
    """Seed assemblies for tests.  The `session` fixture is the in-memory one from conftest."""
    assemblies = [
        Assembly(part_number="138623"),   # len 6 — within window of "138624"
        Assembly(part_number="138624"),   # exact duplicate of query — excluded by design
        Assembly(part_number="138625"),   # len 6 — within window, distance 1
        Assembly(part_number="138700"),   # len 6 — within window, distance 3
        Assembly(part_number="OCTO-QUAD-H1-2.C.1"),  # len 18 — outside window for len-6 query
        Assembly(part_number="ABC"),      # len 3 — outside window
    ]
    for a in assemblies:
        session.add(a)
    session.commit()
    return session


# ---------------------------------------------------------------------------
# P-3: Length-bucket filter
# ---------------------------------------------------------------------------

class TestLengthBucketFilter:
    """Candidates whose length differs from the query by more than LENGTH_WINDOW=3
    should be excluded from Levenshtein computation."""

    def test_long_assembly_excluded(self, session_with_assemblies):
        """'OCTO-QUAD-H1-2.C.1' (len 18) is NOT in results for '138624' (len 6)."""
        results = _similar_assemblies_compute(session_with_assemblies, "138624")
        returned_pns = {r["part_number"] for r in results}
        assert "OCTO-QUAD-H1-2.C.1" not in returned_pns

    def test_short_assembly_excluded(self, session_with_assemblies):
        """'ABC' (len 3) is NOT in results for '138624' (len 6)."""
        results = _similar_assemblies_compute(session_with_assemblies, "138624")
        returned_pns = {r["part_number"] for r in results}
        assert "ABC" not in returned_pns

    def test_same_pn_excluded(self, session_with_assemblies):
        """The query pn itself is excluded even when it exists in the registry."""
        results = _similar_assemblies_compute(session_with_assemblies, "138624")
        returned_pns = {r["part_number"] for r in results}
        assert "138624" not in returned_pns


# ---------------------------------------------------------------------------
# P-3: Single Levenshtein call per candidate
# ---------------------------------------------------------------------------

class TestSingleLevenshteinCall:
    """_similar_assemblies_compute must call _levenshtein exactly once per
    candidate that passes the length filter — never twice."""

    def test_levenshtein_called_once_per_candidate(self, session_with_assemblies):
        """Each candidate (after length filter) triggers exactly one _levenshtein call."""
        import backend.app.api.ingest as mod

        call_log: list[tuple[str, str]] = []

        original_lev = mod._levenshtein

        def counting_levenshtein(a: str, b: str) -> int:
            call_log.append((a, b))
            return original_lev(a, b)

        with patch.object(mod, "_levenshtein", side_effect=counting_levenshtein):
            _similar_assemblies_compute(session_with_assemblies, "138624")

        # Each candidate that passes the length filter must appear exactly once.
        from collections import Counter
        counts = Counter(pair[1] for pair in call_log)  # second arg is the candidate
        for pn, n in counts.items():
            assert n == 1, f"_levenshtein called {n} times for '{pn}', expected 1"


# ---------------------------------------------------------------------------
# P-3: Top-N ordering
# ---------------------------------------------------------------------------

class TestTopNOrdering:
    """Results are ordered ascending by edit distance; top_n=3 by default."""

    def test_results_ordered_ascending_edit_distance(self, session_with_assemblies):
        """Returned list is sorted by edit_distance ascending."""
        results = _similar_assemblies_compute(session_with_assemblies, "138624")
        distances = [r["edit_distance"] for r in results]
        assert distances == sorted(distances)

    def test_top_n_limits_results(self, session_with_assemblies):
        """At most top_n results are returned."""
        results = _similar_assemblies_compute(session_with_assemblies, "138624", top_n=2)
        assert len(results) <= 2

    def test_returns_dicts_with_expected_keys(self, session_with_assemblies):
        """Each result dict has part_number and edit_distance keys."""
        results = _similar_assemblies_compute(session_with_assemblies, "138624")
        for r in results:
            assert "part_number" in r
            assert "edit_distance" in r
            assert isinstance(r["edit_distance"], int)
