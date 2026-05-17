from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from .models import BuildQualifier, BuildType

_BUILD_TYPES = {bt.value.upper(): bt for bt in BuildType}
_BUILD_QUALIFIERS: dict[str, BuildQualifier] = {q.value.upper(): q for q in BuildQualifier}
_LEAD_TIME_RE = re.compile(r"\b(\d+D)\b", re.IGNORECASE)
_CLEAR_KEYWORD_RE = re.compile(
    r"(?:(\d{1,2}/\d{1,2}(?:/\d{4})?)\s*[-\s]\s*(?:clear(?:s|ed)?))"
    r"|(?:(?:clear(?:s|ed)?)\s+(\d{1,2}/\d{1,2}(?:/\d{4})?))",
    re.IGNORECASE,
)
_SPLIT_SUFFIX_LEXICON: tuple[str, ...] = ("par", "bal", "ser")
SPLIT_SUFFIX_RE = re.compile(
    r"-(?P<digits>\d+)(?P<token>" + "|".join(_SPLIT_SUFFIX_LEXICON) + r")$",
    re.IGNORECASE,
)
SO_NUMBER_RE = re.compile(r"SO#", re.IGNORECASE)
_PART_NUMBER_SHAPED_RE = re.compile(r"^[A-Za-z0-9._\-\s]+$")
_REPEAT_RE = re.compile(
    r"^(?P<bt>RONC|ROWC)(?:\s+(?P<ref>[^(]+?))?\s*(?:\(.*)?$",
    re.IGNORECASE,
)
_QUALIFIER_RE = re.compile(
    r"^(?P<q>RWK|REWORK|RMA)(?:\s+(?P<ref>[^(]+?))?\s*(?:\(.*)?$",
    re.IGNORECASE,
)
_SHIPPED_DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%m/%d/%Y")
_PARENS_RE = re.compile(r"\(([^)]+)\)")
_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
_CODE_SEPARATORS_RE = re.compile(r"[\s,/\-]+")


@dataclass(frozen=True)
class JobDecomposition:
    part_number: str
    split_suffix: str | None
    build_type: BuildType
    repeat_reference: str | None
    classification_codes: tuple[str, ...]
    build_qualifier: BuildQualifier | None = None

    def with_overrides(
        self,
        *,
        part_number: str,
        split_suffix: str | None,
    ) -> "JobDecomposition":
        """Return a copy with part_number and split_suffix replaced.

        Pre:  part_number is non-empty.
        Post: all other fields (build_type, repeat_reference,
              classification_codes, build_qualifier) are unchanged.
        Raises: never.
        """
        return JobDecomposition(
            part_number=part_number,
            split_suffix=split_suffix,
            build_type=self.build_type,
            repeat_reference=self.repeat_reference,
            classification_codes=self.classification_codes,
            build_qualifier=self.build_qualifier,
        )


@dataclass(frozen=True)
class DecomposeError:
    """Structured failure from decompose_job_string_with_diagnostic.

    Pre:  raw is the staging row's raw_job string.
    Post: code identifies the rule; message is the user-facing surfaced text;
          recovered_classifications is populated on R1 failures when
          classification tokens were detected in the cell.
    """
    code: Literal[
        "R1_no_classifier",
        "R2_multiple_qualifiers",
        "R3_multi_part_cell",
        "R4_so_number_in_job",
    ]
    message: str
    recovered_classifications: tuple[str, ...] = ()


def _extract_classifications(lines: list[str]) -> tuple[str, ...]:
    codes: list[str] = []
    for line in lines:
        for content in _PARENS_RE.findall(line.strip()):
            for token in _CODE_SEPARATORS_RE.split(content.strip()):
                token = token.strip()
                if _CODE_RE.match(token):
                    codes.append(token.upper())
    seen: set[str] = set()
    out: list[str] = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return tuple(out)


def _parse_build_line(line: str) -> tuple[BuildType, str | None] | None:
    token = line.strip()
    upper = token.upper()
    if upper in _BUILD_TYPES:
        return _BUILD_TYPES[upper], None
    m = _REPEAT_RE.match(token)
    if m is None:
        return None
    bt = {"RONC": BuildType.ronc, "ROWC": BuildType.rowc}[m.group("bt").upper()]
    ref = m.group("ref")
    return bt, (" ".join(ref.split()).lower() if ref else None)


def _parse_qualifier_line(line: str) -> tuple[BuildQualifier, str | None] | None:
    """Parse a single line as a qualifier-with-optional-repeat-ref.

    Pre:  line is a single, possibly whitespace-padded raw cell line.
    Post: returns (qualifier, normalised_repeat_ref) on match; None otherwise.
          repeat_ref is lower-cased and inner whitespace is collapsed (mirrors _parse_build_line).
    Raises: never.
    """
    token = line.strip()
    m = _QUALIFIER_RE.match(token)
    if m is None:
        return None
    qualifier = _BUILD_QUALIFIERS[m.group("q").upper()]
    raw_ref = m.group("ref")
    normalised_ref = " ".join(raw_ref.split()).lower() if raw_ref else None
    return qualifier, normalised_ref


def parse_part_line(line: str) -> tuple[str, str | None]:
    """Apply SPLIT_SUFFIX_RE to a stripped, non-empty line.

    Pre:  line is stripped and non-empty (caller guarantees).
    Post: when SPLIT_SUFFIX_RE matches the tail, split_suffix includes the
          leading "-" and is lower-cased; part_number is the remainder with
          trailing whitespace stripped.  Otherwise part_number is line
          verbatim and split_suffix is None.
    Raises: never.
    """
    m = SPLIT_SUFFIX_RE.search(line)
    if m is None:
        return line, None
    return line[: m.start()].rstrip(), line[m.start():].lower()


def looks_like_multi_part_cell(lines: list[str]) -> bool:
    """Return True when every non-empty line is part-number-shaped and none
    carries a build-type or build-qualifier token.

    Pre:  lines is the result of raw.split("\\n"); raw was non-empty.
    Post: True iff the cell is a multi-part candidate (R3 guard).
          Single-line cells return False (they belong to the single-line
          fallback path).
    Raises: never.
    """
    non_empty = [ln.strip() for ln in lines if ln.strip()]
    if len(non_empty) < 2:
        return False
    for ln in non_empty:
        if _parse_build_line(ln) is not None:
            return False
        if _parse_qualifier_line(ln) is not None:
            return False
        if _PART_NUMBER_SHAPED_RE.match(ln) is None:
            return False
    return True


def decompose_job_string_with_diagnostic(raw: str) -> JobDecomposition | DecomposeError:
    """Decompose a raw JOB cell string into a JobDecomposition, or return a
    DecomposeError with a specific failure code and user-visible message.

    Pre:  raw is a non-empty string.
    Post: returns JobDecomposition on success; DecomposeError (R1–R4) on
          structural failure.  Check order: R4 (SO#) → R3 (multi-part) →
          single-line fallback → R1/R2 from line scan.
    Raises: never.
    """
    if SO_NUMBER_RE.search(raw):
        return DecomposeError(
            code="R4_so_number_in_job",
            message=f"SO# is not allowed in JOB cell: {raw!r}",
        )

    lines = raw.split("\n")

    if looks_like_multi_part_cell(lines):
        return DecomposeError(
            code="R3_multi_part_cell",
            message=f"JOB cell appears to contain multiple part numbers: {raw!r}",
        )

    if len(lines) < 2:
        tokens = raw.strip().split(None, 1)
        if len(tokens) < 2:
            return DecomposeError(
                code="R1_no_classifier",
                message=f"Invalid JOB cell: {raw!r}",
                recovered_classifications=_extract_classifications(lines),
            )
        lines = [tokens[0], tokens[1]]

    line0 = lines[0].strip()
    if not line0:
        return DecomposeError(
            code="R1_no_classifier",
            message=f"Invalid JOB cell: {raw!r}",
            recovered_classifications=_extract_classifications(lines),
        )

    part_number, split_suffix = parse_part_line(line0)

    intermediates: list[str] = []
    build_type: BuildType | None = None
    build_qualifier: BuildQualifier | None = None
    repeat_reference: str | None = None
    last_classified_idx: int = 0

    for idx in range(1, len(lines)):
        line = lines[idx]

        if build_type is None:
            candidate_bt = _parse_build_line(line)
            if candidate_bt is not None:
                build_type, candidate_ref = candidate_bt
                if candidate_ref is not None:
                    repeat_reference = candidate_ref
                last_classified_idx = idx
                continue

        candidate_q = _parse_qualifier_line(line)
        if candidate_q is not None:
            if build_qualifier is not None:
                return DecomposeError(
                    code="R2_multiple_qualifiers",
                    message=f"Multiple build qualifiers in JOB cell: {raw!r}",
                )
            build_qualifier, candidate_q_ref = candidate_q
            if candidate_q_ref is not None and repeat_reference is None:
                repeat_reference = candidate_q_ref
            last_classified_idx = idx
            continue

        stripped = line.strip()
        # Lines starting with '(' are classification tokens — consumed by
        # _extract_classifications below, not intermediates.
        if stripped and not stripped.startswith("("):
            intermediates.append(stripped.lower())

    if build_type is None and build_qualifier is None:
        return DecomposeError(
            code="R1_no_classifier",
            message=f"Invalid JOB cell: {raw!r}",
            recovered_classifications=_extract_classifications(lines),
        )

    if build_type is None and build_qualifier is not None:
        # R5 default: qualifier-only cell implies build_type=new
        build_type = BuildType.new

    if intermediates:
        parts = [split_suffix] if split_suffix else []
        parts.extend(intermediates)
        split_suffix = " ".join(parts)

    classifications = _extract_classifications(lines[last_classified_idx + 1:])

    return JobDecomposition(
        part_number=part_number,
        split_suffix=split_suffix,
        build_type=build_type,
        repeat_reference=repeat_reference,
        classification_codes=classifications,
        build_qualifier=build_qualifier,
    )


def decompose_job_string(raw: str) -> JobDecomposition | None:
    """Back-compat wrapper. Returns None on any structural failure; loses error specificity."""
    out = decompose_job_string_with_diagnostic(raw)
    return out if isinstance(out, JobDecomposition) else None


def extract_ship_fields(raw: str) -> tuple[str | None, str | None]:
    head = raw[:5] if raw else None
    lead = _LEAD_TIME_RE.search(raw)
    lead_time = lead.group(1).upper() if lead else None
    return head, lead_time


def extract_shipped_date(raw: str) -> date | None:
    if not raw:
        return None
    candidate = raw.strip()[:10]
    if len(candidate) < 10:
        return None
    for fmt in _SHIPPED_DATE_FORMATS:
        try:
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def extract_clear_date_from_notes(raw: str) -> str | None:
    for line in raw.split("\n"):
        m = _CLEAR_KEYWORD_RE.search(line)
        if m:
            return m.group(1) or m.group(2)
    return None
