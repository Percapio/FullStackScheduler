from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from .models import BuildType

_BUILD_TYPES = {bt.value.upper(): bt for bt in BuildType}
_LEAD_TIME_RE = re.compile(r"\b(\d+D)\b", re.IGNORECASE)
_CLEAR_KEYWORD_RE = re.compile(
    r"(?:(\d{1,2}/\d{1,2}(?:/\d{4})?)\s*[-\s]\s*(?:clear(?:s|ed)?))"
    r"|(?:(?:clear(?:s|ed)?)\s+(\d{1,2}/\d{1,2}(?:/\d{4})?))",
    re.IGNORECASE,
)
_SUFFIX_RE = re.compile(r"^(?P<part>.+?)(?P<suffix>[-. ][A-Za-z0-9]+)?$")
_REPEAT_RE = re.compile(
    r"^(?P<bt>RONC|ROWC|RWK)(?:\s+(?P<ref>[^(]+?))?\s*(?:\(.*)?$",
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
    bt = {"RONC": BuildType.ronc, "ROWC": BuildType.rowc, "RWK": BuildType.rwk}[m.group("bt").upper()]
    ref = m.group("ref")
    return bt, (" ".join(ref.split()).lower() if ref else None)


def decompose_job_string(raw: str) -> JobDecomposition | None:
    lines = raw.split("\n")
    if len(lines) < 2:
        tokens = raw.strip().split(None, 1)
        if len(tokens) < 2:
            return None
        lines = [tokens[0], tokens[1]]

    line0 = lines[0].strip()
    if not line0:
        return None

    m = _SUFFIX_RE.match(line0)
    part_number = m.group("part").strip() if m else line0
    raw_suffix = m.group("suffix") if m else None
    split_suffix = raw_suffix.strip().lower() if raw_suffix else None

    intermediates: list[str] = []
    build_idx: int | None = None
    parsed: tuple[BuildType, str | None] | None = None
    for idx in range(1, len(lines)):
        candidate = _parse_build_line(lines[idx])
        if candidate is not None:
            build_idx = idx
            parsed = candidate
            break
        stripped = lines[idx].strip()
        if stripped:
            intermediates.append(stripped.lower())
    if parsed is None or build_idx is None:
        return None
    build_type, repeat_reference = parsed

    if intermediates:
        parts = [split_suffix] if split_suffix else []
        parts.extend(intermediates)
        split_suffix = " ".join(parts)

    classifications = _extract_classifications(lines[build_idx + 1:])

    return JobDecomposition(
        part_number=part_number,
        split_suffix=split_suffix,
        build_type=build_type,
        repeat_reference=repeat_reference,
        classification_codes=classifications,
    )


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
