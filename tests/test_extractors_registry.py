"""Tests for Phase 18c §5 — registry-driven non-B# decomposition.

Covers the three rules in decompose_part_line_by_registry:
  F1: longest-prefix-wins (not shadowed by shorter registry entry)
  F2: empty registry → verbatim fallback
  F3: rev-token guard disqualifies a registry match
  F4: longest-match beats shorter match for same part family

And the composer (decompose_part_line) priority rules:
  Composer Rule 1: shape rule wins over registry
  Composer Rule 2: shape does not fire, registry does → registry result
  Composer Rule 3: neither fires → verbatim
"""
from __future__ import annotations

import pytest

from backend.app.extractors import decompose_part_line, decompose_part_line_by_registry


class TestDecomposePartLineByRegistry:
    def test_registry_prefix_match_returns_longest(self):
        """F4: longest-prefix-wins.

        Registry contains both 'ACME-100' and 'ACME-100A'; input 'ACME-100A-par'
        must return ('ACME-100A', '-par'), not ('ACME-100', 'A-par').
        """
        registry = {"ACME-100", "ACME-100A"}
        part, suffix = decompose_part_line_by_registry("ACME-100A-par", registry)
        assert part == "ACME-100A"
        assert suffix == "-par"

    def test_registry_empty_returns_verbatim(self):
        """F2: No candidates → (line, None)."""
        part, suffix = decompose_part_line_by_registry("OCTO-QUAD-H1-2.C.1-par", set())
        assert part == "OCTO-QUAD-H1-2.C.1-par"
        assert suffix is None

    def test_registry_rev_substring_disqualifies(self):
        """F3: rev-token guard fires — match is skipped, verbatim returned.

        'OCTO-QUAD-H1-2.C.1_REV.3' with 'OCTO-QUAD-H1-2.C.1' in registry
        must return (line, None) because the trailing token contains a rev marker.
        """
        registry = {"OCTO-QUAD-H1-2.C.1"}
        line = "OCTO-QUAD-H1-2.C.1_REV.3"
        part, suffix = decompose_part_line_by_registry(line, registry)
        assert part == line
        assert suffix is None


class TestDecomposePartLineComposer:
    def test_composer_shape_rule_wins_over_registry(self):
        """Rule 1: pure-digit input '118107' returns ('118107', None) via shape rule
        even when registry contains '118' as a prefix.
        """
        registry = {"118"}
        part, suffix = decompose_part_line("118107", registry)
        assert part == "118107"
        assert suffix is None

    def test_composer_shape_rule_does_not_fire_registry_does(self):
        """Rule 2: shape does not fire, registry match wins.

        'OCTO-QUAD-H1-2.C.1-par' with registry containing 'OCTO-QUAD-H1-2.C.1'
        returns ('OCTO-QUAD-H1-2.C.1', '-par').
        """
        registry = {"OCTO-QUAD-H1-2.C.1"}
        part, suffix = decompose_part_line("OCTO-QUAD-H1-2.C.1-par", registry)
        assert part == "OCTO-QUAD-H1-2.C.1"
        assert suffix == "-par"

    def test_composer_neither_rule_fires_verbatim_returned(self):
        """Rule 3: no shape, no registry match → verbatim.

        'OCTO-QUAD-H1-2.C.1' with empty registry returns ('OCTO-QUAD-H1-2.C.1', None).
        """
        part, suffix = decompose_part_line("OCTO-QUAD-H1-2.C.1", set())
        assert part == "OCTO-QUAD-H1-2.C.1"
        assert suffix is None
