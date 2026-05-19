"""Tests for Phase 19 §4 — decompose_part_line_by_shape (5/6-digit shape rule).

Each test exercises one row of the specification table from TDD §11.
Naming convention: Method_Condition_ExpectedOutcome.
"""
from backend.app.extractors import decompose_part_line_by_shape


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


def test_four_digit_pure_run_returns_verbatim():
    """Four-digit run does not match 5/6-digit shape; returned verbatim."""
    assert decompose_part_line_by_shape("1234") == ("1234", None)


def test_seven_digit_pure_run_returns_verbatim():
    """Seven-digit run does not match 5/6-digit shape (Phase 19); returned verbatim."""
    assert decompose_part_line_by_shape("1181070") == ("1181070", None)
    assert decompose_part_line_by_shape("1234567") == ("1234567", None)


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
# Phase 19 — ex-R3 fast-fail cases
# ---------------------------------------------------------------------------

def test_letter_prefix_abc_returns_verbatim():
    """Lines starting with letters fast-fail to verbatim (no shape match)."""
    assert decompose_part_line_by_shape("ABC-12345") == ("ABC-12345", None)


def test_rev_guard_on_five_digit_with_rev_tail():
    """5-digit prefix followed by '-rev1' is disqualified by the rev guard.

    Without the guard, '12345' would match the shape rule and '-rev1' would
    become the split_suffix.  The rev guard must fire first.
    """
    assert decompose_part_line_by_shape("12345-rev1") == ("12345-rev1", None)

