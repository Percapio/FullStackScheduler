"""Tests for Phase 18b §5 — decompose_part_line_by_shape and shape_rule_would_fire.

Each test exercises one row of the specification table from TDD §11.
Naming convention: Method_Condition_ExpectedOutcome.
"""
from backend.app.extractors import decompose_part_line_by_shape, shape_rule_would_fire


# ---------------------------------------------------------------------------
# Basic digit-only inputs
# ---------------------------------------------------------------------------

def test_pure_digits_no_suffix():
    """Pure digit string with no separator returns (digits, None)."""
    assert decompose_part_line_by_shape("118107") == ("118107", None)


def test_pure_digits_with_numeric_suffix():
    """Digits followed by hyphen-digit suffix returns (digits, '-N')."""
    assert decompose_part_line_by_shape("118107-2") == ("118107", "-2")


def test_pure_digits_with_lexical_suffix():
    """Digits followed by hyphen-alpha suffix returns (digits, '-alpha')."""
    assert decompose_part_line_by_shape("118107-par") == ("118107", "-par")


# ---------------------------------------------------------------------------
# REV disqualification
# ---------------------------------------------------------------------------

def test_rev_substring_disqualifies_uppercase():
    """'REV' anywhere in line disqualifies; returned verbatim."""
    assert decompose_part_line_by_shape("118107_REV.A") == ("118107_REV.A", None)


def test_rev_substring_disqualifies_mixed_case():
    """'rev' in the middle of an extended suffix still disqualifies."""
    assert decompose_part_line_by_shape("0000004-203_REV.3") == ("0000004-203_REV.3", None)


# ---------------------------------------------------------------------------
# Length variations
# ---------------------------------------------------------------------------

def test_five_digit_b_number_qualifies():
    """Five-digit values qualify (legacy B# format per E4)."""
    assert decompose_part_line_by_shape("99000-bal") == ("99000", "-bal")


def test_seven_digit_pure_run_qualifies_with_no_suffix():
    """Seven-digit run with no suffix qualifies (Audit Finding 2)."""
    assert decompose_part_line_by_shape("1181070") == ("1181070", None)


# ---------------------------------------------------------------------------
# Leading-letter disqualification
# ---------------------------------------------------------------------------

def test_leading_letters_disqualify():
    """A line starting with non-digits falls through to verbatim return."""
    assert decompose_part_line_by_shape("OCTO-QUAD-H1-2.C.1") == ("OCTO-QUAD-H1-2.C.1", None)


def test_letter_adjacent_to_digits_disqualifies():
    """Trailing letter adjacent to digits (no separator) disqualifies."""
    assert decompose_part_line_by_shape("118107A") == ("118107A", None)


# ---------------------------------------------------------------------------
# Separator class
# ---------------------------------------------------------------------------

def test_separator_class_space_disqualifies():
    """Space is not a valid separator; line returned verbatim."""
    assert decompose_part_line_by_shape("118107 2") == ("118107 2", None)


def test_separator_class_dot_qualifies():
    """Dot is a valid separator class member."""
    assert decompose_part_line_by_shape("118107.2") == ("118107", ".2")


def test_separator_class_underscore_qualifies():
    """Underscore is a valid separator class member."""
    assert decompose_part_line_by_shape("118107_2") == ("118107", "_2")


# ---------------------------------------------------------------------------
# Bare trailing separator (E13)
# ---------------------------------------------------------------------------

def test_bare_trailing_separator_dot_qualifies_as_suffix():
    """A trailing '.' with nothing after it is returned as the suffix (E13)."""
    assert decompose_part_line_by_shape("118107.") == ("118107", ".")


def test_bare_trailing_separator_hyphen_qualifies_as_suffix():
    """A trailing '-' with nothing after it is returned as the suffix (E13)."""
    assert decompose_part_line_by_shape("118107-") == ("118107", "-")


def test_bare_trailing_separator_underscore_qualifies_as_suffix():
    """A trailing '_' with nothing after it is returned as the suffix (E13)."""
    assert decompose_part_line_by_shape("118107_") == ("118107", "_")


# ---------------------------------------------------------------------------
# shape_rule_would_fire — Phase 18b Patch 01 P-1.3
# Regression for F1: the predicate must report True for pure-digit inputs
# even though decompose_part_line_by_shape returns the same string unchanged.
# ---------------------------------------------------------------------------

def test_shape_rule_would_fire_returns_true_for_pure_digit_canonical():
    """Pure-digit cell lines with no separator must report shape_rule_would_fire=True.

    Regression for Phase 18b Patch 01 F1: the old predicate (part != first_line)
    returned False for these inputs because the digit run equals the input line.
    """
    assert shape_rule_would_fire("118107")  is True
    assert shape_rule_would_fire("99000")   is True
    assert shape_rule_would_fire("1181070") is True


def test_shape_rule_would_fire_returns_false_for_rev_disqualified():
    """Lines containing the rev token are not rule-fired."""
    assert shape_rule_would_fire("118107_REV.A") is False


def test_shape_rule_would_fire_returns_false_for_non_digit_lead():
    """Lines starting with non-digits are not rule-fired."""
    assert shape_rule_would_fire("OCTO-QUAD-H1") is False


def test_shape_rule_would_fire_returns_true_for_digits_with_suffix():
    """Digit lines with valid separators are rule-fired."""
    assert shape_rule_would_fire("118107-2") is True
    assert shape_rule_would_fire("118107.")  is True


def test_shape_rule_would_fire_returns_false_for_empty_string():
    """Empty string returns False without raising."""
    assert shape_rule_would_fire("") is False
