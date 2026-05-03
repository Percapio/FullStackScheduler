from datetime import date

import pytest

from backend.app.extractors import (
    DecomposeError,
    JobDecomposition,
    decompose_job_string,
    decompose_job_string_with_diagnostic,
    extract_clear_date_from_notes,
    extract_ship_fields,
    extract_shipped_date,
)
from backend.app.models import BuildQualifier, BuildType


# ---- JOB decomposition -------------------------------------------------------

def test_decompose_job_with_classification():
    result = decompose_job_string("137845\nNEW\n(ITAR)")
    assert result == JobDecomposition("137845", None, BuildType.new, None, ("ITAR",))


def test_decompose_job_without_classification():
    result = decompose_job_string("137845\nRONC")
    assert result == JobDecomposition("137845", None, BuildType.ronc, None, ())


def test_decompose_job_multiple_classifications():
    result = decompose_job_string("137845\nROWC\n(ITAR)\n(EAR99)")
    assert result == JobDecomposition("137845", None, BuildType.rowc, None, ("ITAR", "EAR99"))


@pytest.mark.parametrize("raw_job,expected", [
    ("137845\nNEW\n(ITAR)", ("ITAR",)),
    ("137845\nNEW\n(CUI)", ("CUI",)),
    ("137845\nNEW\n(AS9100)", ("AS9100",)),
    ("137845\nNEW\n(DoD)", ("DOD",)),
    ("137845\nNEW\n(ITAR) (CUI)", ("ITAR", "CUI")),
    ("137845\nNEW\n(ITAR, CUI)", ("ITAR", "CUI")),
    ("137845\nNEW\n(ITAR/CUI)", ("ITAR", "CUI")),
    ("137845\nNEW\n(134705)", ()),
    ("137845\nNEW\n(ITAR 134705)", ("ITAR",)),
    ("137845\nNEW\n(cui)", ("CUI",)),
    ("137845\nNEW\n(AS9100-ITAR)", ("AS9100", "ITAR")),
    ("137845\nNEW\n(AS9100-ITAR-CUI)", ("AS9100", "ITAR", "CUI")),
    ("137845\nNEW\n(DoD-ITAR-CUI)", ("DOD", "ITAR", "CUI")),
])
def test_decompose_classifications(raw_job, expected):
    result = decompose_job_string(raw_job)
    assert result is not None
    assert result.classification_codes == expected


def test_decompose_job_missing_build_type_returns_none():
    assert decompose_job_string("137845") is None


def test_decompose_job_unknown_build_type_returns_none():
    assert decompose_job_string("137845\nFOO") is None


# ---- split_suffix parsing ----------------------------------------------------

@pytest.mark.parametrize("raw,expected_part,expected_suffix", [
    ("137845\nNEW", "137845", None),
    ("137845-1\nNEW", "137845", "-1"),
    ("137845-1par\nNEW", "137845", "-1par"),
    ("137845-3bal\nNEW", "137845", "-3bal"),
    ("137845-A\nNEW", "137845", "-a"),
    ("137845.1\nNEW", "137845", ".1"),
    ("137845 -1\nNEW", "137845", "-1"),
    ("ABC-123-1par\nNEW", "ABC-123", "-1par"),
    ("ABC-123.A\nNEW", "ABC-123", ".a"),
])
def test_decompose_parses_split_suffix(raw, expected_part, expected_suffix):
    result = decompose_job_string(raw)
    assert result is not None
    assert result.part_number == expected_part
    assert result.split_suffix == expected_suffix


# ---- repeat_reference parsing ------------------------------------------------

@pytest.mark.parametrize("raw,expected_bt,expected_ref", [
    ("137845\nRONC", BuildType.ronc, None),
    ("137845\nROWC", BuildType.rowc, None),
    ("137845\nRONC 123456", BuildType.ronc, "123456"),
    ("137845\nROWC 123456", BuildType.rowc, "123456"),
    ("137845\nRONC 1st", BuildType.ronc, "1st"),
    ("137845\nROWC 1st", BuildType.rowc, "1st"),
    ("137845\nRONC 1ST", BuildType.ronc, "1st"),
    ("137845\nROWC 1st Article", BuildType.rowc, "1st article"),
    ("137845\nRONC first-article", BuildType.ronc, "first-article"),
    ("137845\nROWC 1st   Article", BuildType.rowc, "1st article"),
    ("137845\nROWC 1ST ARTICLE", BuildType.rowc, "1st article"),
    ("137845\nROWC 1st Article (ITAR)", BuildType.rowc, "1st article"),
    ("137845\nRONC (ITAR)", BuildType.ronc, None),
])
def test_decompose_parses_repeat_reference(raw, expected_bt, expected_ref):
    result = decompose_job_string(raw)
    assert result is not None
    assert result.build_type == expected_bt
    assert result.repeat_reference == expected_ref


def test_decompose_rejects_unknown_build_token():
    assert decompose_job_string("137845\nFOO") is None


# ---- whitespace-delimited JOB cells -----------------------------------------

@pytest.mark.parametrize("raw,expected_part,expected_bt,expected_ref", [
    ("128764 NEW",              "128764", BuildType.new,  None),
    ("128764\tNEW",             "128764", BuildType.new,  None),
    ("128764-1par NEW",         "128764", BuildType.new,  None),
    ("128764 RONC 123456",      "128764", BuildType.ronc, "123456"),
    ("128764 ROWC 1st Article", "128764", BuildType.rowc, "1st article"),
])
def test_decompose_whitespace_delimited_job_cell(
    raw, expected_part, expected_bt, expected_ref,
):
    result = decompose_job_string(raw)
    assert result is not None
    assert result.part_number == expected_part
    assert result.build_type == expected_bt
    assert result.repeat_reference == expected_ref


def test_decompose_bare_part_still_rejected():
    assert decompose_job_string("128764") is None


@pytest.mark.parametrize("raw", [
    "107703-1",
    "128764-1par",
])
def test_decompose_bare_part_with_suffix_rejected(raw):
    assert decompose_job_string(raw) is None


# ---- intermediate annotations fold into split_suffix -------------------------

def test_decompose_single_intermediate_folds_into_split_suffix():
    raw = "131635-2nd\n4BAL\nROWC 1st Article"
    result = decompose_job_string(raw)
    assert result is not None
    assert result.part_number == "131635"
    assert result.split_suffix == "-2nd 4bal"
    assert result.build_type == BuildType.rowc
    assert result.repeat_reference == "1st article"
    assert result.classification_codes == ()


def test_decompose_multiple_intermediates_fold_in_order():
    raw = "137845\nfoo\nbar\nRONC 123456\n(ITAR)"
    result = decompose_job_string(raw)
    assert result is not None
    assert result.split_suffix == "foo bar"
    assert result.build_type == BuildType.ronc
    assert result.repeat_reference == "123456"
    assert result.classification_codes == ("ITAR",)


def test_decompose_intermediate_only_no_line0_suffix():
    raw = "137845\n4BAL\nROWC"
    result = decompose_job_string(raw)
    assert result is not None
    assert result.split_suffix == "4bal"
    assert result.repeat_reference is None


# ---- lead-time extraction ----------------------------------------------------

@pytest.mark.parametrize("token", ["1D", "2D", "3D", "5D", "7D", "10D", "15D", "20D"])
def test_extract_lead_time_canonical_tokens(token: str):
    _, lead = extract_ship_fields(f"4/17\n{token}")
    assert lead == token


def test_extract_lead_time_absent():
    _, lead = extract_ship_fields("4/17")
    assert lead is None


# ---- ship_date_text extraction -----------------------------------------------

def test_extract_ship_fields_returns_head_slice():
    head, _ = extract_ship_fields("4/17\n15D")
    assert head == "4/17\n"


def test_extract_ship_fields_short_input():
    head, _ = extract_ship_fields("4/17")
    assert head == "4/17"


@pytest.mark.parametrize("raw,expected_head,expected_lead", [
    ("???",      "???",   None),
    ("HOLD",     "HOLD",  None),
    ("TBD 30D",  "TBD 3", "30D"),
    ("",         None,    None),
])
def test_extract_ship_fields_tolerates_sentinel_text(raw, expected_head, expected_lead):
    head, lead = extract_ship_fields(raw)
    assert head == expected_head
    assert lead == expected_lead


# ---- extract_shipped_date ----------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("2026-04-17",               date(2026, 4, 17)),
    ("2026-04-17 00:00:00",      date(2026, 4, 17)),
    ("04/17/2026",               date(2026, 4, 17)),
    ("04/17/2026 via FedEx",     date(2026, 4, 17)),
    ("4/17/2026",                None),
    ("2026/04/17",               None),
    ("",                         None),
])
def test_extract_shipped_date_grammars(raw, expected):
    assert extract_shipped_date(raw) == expected


# ---- clear-date extraction ---------------------------------------------------

def test_clear_date_from_pcb_notes_keyword_before_date():
    assert extract_clear_date_from_notes("clear 4/14") == "4/14"


def test_clear_date_from_kit_notes_date_before_keyword():
    assert extract_clear_date_from_notes("4/14 clear to ship") == "4/14"


def test_clear_date_ignores_non_clear_dates():
    assert extract_clear_date_from_notes(
        "4/14 - short SMT IC3 due 4/17\nshort THT X1 & X4 no ETA"
    ) is None


def test_clear_date_case_insensitive():
    assert extract_clear_date_from_notes("CLEARED 4/14") == "4/14"


def test_clear_date_with_year():
    assert extract_clear_date_from_notes("clear 4/14/2026") == "4/14/2026"


# ---- build qualifier recognition (Phase 4) -----------------------------------

def test_decompose_recognises_rwk_as_qualifier_with_default_build_type():
    """R5: qualifier-only cell defaults build_type to NEW (D1 fix)."""
    result = decompose_job_string("138924\nRWK")
    assert result is not None
    assert result.part_number == "138924"
    assert result.build_type == BuildType.new
    assert result.build_qualifier == BuildQualifier.rwk
    assert result.repeat_reference is None


@pytest.mark.parametrize("raw,expected_q", [
    ("138924\nREWORK",  BuildQualifier.rework),
    ("138924\nRMA",     BuildQualifier.rma),
    ("138924\nrework",  BuildQualifier.rework),
    ("138924\nrma",     BuildQualifier.rma),
])
def test_decompose_recognises_rework_and_rma(raw, expected_q):
    result = decompose_job_string(raw)
    assert result is not None
    assert result.build_type == BuildType.new
    assert result.build_qualifier == expected_q


def test_decompose_combines_build_type_and_qualifier():
    """R3: both build type and qualifier lines may coexist."""
    result = decompose_job_string("138924\nNEW\nRWK")
    assert result is not None
    assert result.build_type == BuildType.new
    assert result.build_qualifier == BuildQualifier.rwk


def test_decompose_qualifier_with_repeat_reference():
    """Repeat reference on qualifier line is captured when no build-type ref precedes it."""
    result = decompose_job_string("138924\nRWK 123456")
    assert result is not None
    assert result.build_qualifier == BuildQualifier.rwk
    assert result.repeat_reference == "123456"


def test_decompose_build_type_ref_takes_precedence_over_qualifier_ref():
    """First-writer-wins: build-type line's repeat_reference wins over qualifier line's ref."""
    result = decompose_job_string("138924\nROWC 100\nRWK 200")
    assert result is not None
    assert result.build_type == BuildType.rowc
    assert result.repeat_reference == "100"
    assert result.build_qualifier == BuildQualifier.rwk


def test_decompose_rejects_multiple_qualifiers():
    """R2: two qualifier lines in the same cell → DecomposeError with R2 code."""
    result = decompose_job_string_with_diagnostic("138924\nRWK\nREWORK")
    assert isinstance(result, DecomposeError)
    assert result.code == "R2_multiple_qualifiers"
    assert "138924" in result.message


def test_decompose_multiple_qualifiers_returns_none_via_wrapper():
    """decompose_job_string (back-compat wrapper) returns None on R2."""
    assert decompose_job_string("138924\nRWK\nREWORK") is None


def test_decompose_qualifier_does_not_set_build_qualifier_on_unqualified_cell():
    """Cells without a qualifier token must have build_qualifier=None."""
    result = decompose_job_string("137845\nNEW")
    assert result is not None
    assert result.build_qualifier is None


@pytest.mark.parametrize("raw,expected_part,expected_q,expected_ref", [
    ("128764 RWK",       "128764", BuildQualifier.rwk,    None),
    ("128764 RWK 123456","128764", BuildQualifier.rwk,    "123456"),
    ("128764 REWORK",    "128764", BuildQualifier.rework, None),
    ("128764 RMA",       "128764", BuildQualifier.rma,    None),
])
def test_decompose_whitespace_delimited_qualifier_cell(raw, expected_part, expected_q, expected_ref):
    """Whitespace-delimited qualifier cells parse correctly via the two-token fallback."""
    result = decompose_job_string(raw)
    assert result is not None
    assert result.part_number == expected_part
    assert result.build_type == BuildType.new
    assert result.build_qualifier == expected_q
    assert result.repeat_reference == expected_ref
