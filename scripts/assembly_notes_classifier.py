"""
2nd/3rd OP Keyword Classifier
Reads assembly notes from schedule.db and emits category_matches.json.

Architecture: Architecture/20260513-AssemblyNotesKeywordClassifier.md (r2)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

import yaml  # PyYAML

# ---------------------------------------------------------------------------
# Domain exceptions
# ---------------------------------------------------------------------------


class DatabaseUnavailable(Exception):
    """DB file absent or not a SQLite database."""


class SchemaMismatch(Exception):
    """Required table / columns not found."""


class ScanInterrupted(Exception):
    """I/O error or lock timeout during the DB scan."""


class OutputDirectoryMissing(Exception):
    """output_dir does not exist; caller must create it."""


class OutputWriteFailed(Exception):
    """Atomic write failed (disk full, permission denied, fsync error)."""


class TaxonomyNotFound(Exception):
    """Taxonomy YAML path does not resolve to a readable file."""


class TaxonomyMalformed(Exception):
    """YAML parse error in the taxonomy file."""


class TaxonomyInvalid(Exception):
    """Semantic validation failure (empty category, missing required field, etc.)."""


# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


class Span(NamedTuple):
    """Inclusive-start, exclusive-end character offsets in preprocessed_note."""

    start: int
    end: int


@dataclass(frozen=True)
class DisqualifierRule:
    keyword: str
    not_followed_by: str
    max_gap_chars: int = 20


@dataclass(frozen=True)
class KeywordCategory:
    name: str
    keywords: tuple[str, ...]
    disqualifiers: tuple[DisqualifierRule, ...]


@dataclass(frozen=True)
class Taxonomy:
    categories: tuple[KeywordCategory, ...]
    global_negators: tuple[str, ...]
    strip_strikethrough: bool


@dataclass(frozen=True)
class MatchedPart:
    part_number: str
    matched_keywords: tuple[str, ...]  # sorted, deduped, taxonomy case


@dataclass
class Suppression:
    part_number: str
    category: str
    keyword: str
    reason: str  # "negation" | "disqualifier" | "strikethrough"
    context_excerpt: str


@dataclass
class RunReport:
    started_at: str
    finished_at: str
    db_path: str
    taxonomy_version: str
    rows_scanned: int
    rows_skipped_null_notes: int
    rows_with_classifier_error: int
    suppressions: list[Suppression]
    per_category_counts: dict[str, int]


# ---------------------------------------------------------------------------
# Compiled taxonomy (internal — not part of the public Taxonomy type)
# ---------------------------------------------------------------------------


@dataclass
class _CompiledCategory:
    """Holds a KeywordCategory alongside its compiled patterns."""

    source: KeywordCategory
    # keyword → compiled case-insensitive whole-word pattern
    keyword_patterns: dict[str, re.Pattern[str]]
    # per-disqualifier-rule: (rule, keyword_pattern, not_followed_by_pattern)
    disqualifier_patterns: list[tuple[DisqualifierRule, re.Pattern[str], re.Pattern[str]]]


@dataclass
class _CompiledTaxonomy:
    source: Taxonomy
    categories: list[_CompiledCategory]
    # negator → compiled pattern
    negator_patterns: list[tuple[str, re.Pattern[str]]]
    strikethrough_pattern: re.Pattern[str]


# ---------------------------------------------------------------------------
# §6.1 — Taxonomy loader
# ---------------------------------------------------------------------------


def load_taxonomy(taxonomy_path: Path) -> _CompiledTaxonomy:
    """Load and compile the taxonomy YAML.

    Pre: taxonomy_path resolves to a readable YAML file.
    Post: returned _CompiledTaxonomy has at least one category; every keyword
          and negator is non-empty; all literals are regex-escaped before
          pattern compilation.
    Raises: TaxonomyNotFound, TaxonomyMalformed, TaxonomyInvalid.
    """
    if not taxonomy_path.exists():
        raise TaxonomyNotFound(f"Taxonomy file not found: {taxonomy_path.resolve()}")

    try:
        raw_yaml = taxonomy_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TaxonomyNotFound(f"Cannot read taxonomy file: {exc}") from exc

    try:
        doc = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        raise TaxonomyMalformed(f"YAML parse error in {taxonomy_path}: {exc}") from exc

    if not isinstance(doc, dict):
        raise TaxonomyMalformed("Taxonomy root must be a YAML mapping.")

    strip_strikethrough = bool(doc.get("strip_strikethrough", True))

    raw_negators = doc.get("global_negators", [])
    if not isinstance(raw_negators, list):
        raise TaxonomyInvalid("global_negators must be a YAML sequence.")
    negators: list[str] = []
    for n in raw_negators:
        if isinstance(n, bool):
            # YAML 1.1 parses bare 'no'/'yes' as booleans; require the user
            # to quote them in the taxonomy file.
            raise TaxonomyInvalid(
                f"global_negators entry {n!r} was parsed as a boolean by PyYAML. "
                "Quote the value in the taxonomy YAML (e.g. \"no\" instead of no)."
            )
        s = str(n).strip()
        if not s:
            raise TaxonomyInvalid("global_negators contains an empty entry.")
        negators.append(s)

    raw_categories = doc.get("categories", [])
    if not isinstance(raw_categories, list) or not raw_categories:
        raise TaxonomyInvalid("taxonomy must have at least one category.")

    parsed_categories: list[KeywordCategory] = []
    seen_names: set[str] = set()

    for cat_doc in raw_categories:
        if not isinstance(cat_doc, dict):
            raise TaxonomyMalformed("Each category entry must be a YAML mapping.")

        name = str(cat_doc.get("name", "")).strip()
        if not name:
            raise TaxonomyInvalid("Category is missing a non-empty 'name' field.")
        if name in seen_names:
            raise TaxonomyInvalid(f"Duplicate category name: {name!r}.")
        seen_names.add(name)

        raw_kws = cat_doc.get("keywords", [])
        if not isinstance(raw_kws, list) or not raw_kws:
            raise TaxonomyInvalid(f"Category {name!r} must have at least one keyword.")
        keywords: list[str] = []
        for kw in raw_kws:
            s = str(kw).strip()
            if not s:
                raise TaxonomyInvalid(f"Category {name!r} has an empty keyword entry.")
            keywords.append(s)

        raw_dqs = cat_doc.get("disqualifiers", [])
        if not isinstance(raw_dqs, list):
            raise TaxonomyInvalid(f"Category {name!r} 'disqualifiers' must be a YAML sequence.")
        disqualifiers: list[DisqualifierRule] = []
        for dq_doc in raw_dqs:
            if not isinstance(dq_doc, dict):
                raise TaxonomyMalformed(f"Disqualifier in category {name!r} must be a mapping.")
            dq_keyword = str(dq_doc.get("keyword", "")).strip()
            dq_not_followed = str(dq_doc.get("not_followed_by", "")).strip()
            if not dq_keyword:
                raise TaxonomyInvalid(
                    f"Disqualifier in category {name!r} is missing required field 'keyword'."
                )
            if not dq_not_followed:
                raise TaxonomyInvalid(
                    f"Disqualifier in category {name!r} is missing required field 'not_followed_by'."
                )
            raw_gap = dq_doc.get("max_gap_chars", 20)
            try:
                gap = int(raw_gap)
                if gap <= 0:
                    raise ValueError
            except (TypeError, ValueError) as exc:
                raise TaxonomyInvalid(
                    f"Disqualifier 'max_gap_chars' in category {name!r} must be a positive integer."
                ) from exc
            disqualifiers.append(DisqualifierRule(dq_keyword, dq_not_followed, gap))

        parsed_categories.append(
            KeywordCategory(name, tuple(keywords), tuple(disqualifiers))
        )

    taxonomy = Taxonomy(
        categories=tuple(parsed_categories),
        global_negators=tuple(negators),
        strip_strikethrough=strip_strikethrough,
    )
    return _compile_taxonomy(taxonomy)


def _compile_taxonomy(taxonomy: Taxonomy) -> _CompiledTaxonomy:
    """Compile all literal strings into case-insensitive whole-word regex patterns."""

    def _word_pattern(literal: str) -> re.Pattern[str]:
        return re.compile(r"\b" + re.escape(literal) + r"\b", re.IGNORECASE)

    negator_patterns = [
        (neg, _word_pattern(neg)) for neg in taxonomy.global_negators
    ]

    compiled_categories: list[_CompiledCategory] = []
    for cat in taxonomy.categories:
        kw_patterns = {kw: _word_pattern(kw) for kw in cat.keywords}
        dq_patterns: list[tuple[DisqualifierRule, re.Pattern[str], re.Pattern[str]]] = []
        for rule in cat.disqualifiers:
            dq_patterns.append(
                (rule, _word_pattern(rule.keyword), _word_pattern(rule.not_followed_by))
            )
        compiled_categories.append(
            _CompiledCategory(cat, kw_patterns, dq_patterns)
        )

    # Non-greedy strikethrough pattern (§6.2)
    strikethrough_pattern = re.compile(r"~~.*?~~", re.DOTALL)

    return _CompiledTaxonomy(
        source=taxonomy,
        categories=compiled_categories,
        negator_patterns=negator_patterns,
        strikethrough_pattern=strikethrough_pattern,
    )


def _sha256_file(path: Path) -> str:
    """Return the hex SHA256 digest of a file's byte content."""
    digest = hashlib.sha256(path.read_bytes())
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# §6.2 — Note preprocessor
# ---------------------------------------------------------------------------


def preprocess_note(
    raw_note: str | None,
    strip_strikethrough: bool,
    compiled: _CompiledTaxonomy,
    part_number: str = "",
) -> str:
    """Normalize raw_note for keyword matching.

    Pre: raw_note is the column value (Text or None).
    Post: returns text with no '~~...~~' regions (when strip_strikethrough is
          True). Returns '' when raw_note is None.
          Unclosed '~~' markers are left in place; a warning is printed
          identifying part_number.
    Raises: never.
    """
    if raw_note is None:
        return ""

    text = raw_note

    if strip_strikethrough:
        # Non-greedy strip of ~~...~~ sections.
        text = compiled.strikethrough_pattern.sub("", text)

        # Warn if a bare unclosed '~~' marker remains after stripping all
        # complete pairs. A remaining '~~' count that is odd signals an
        # unclosed marker.
        remaining_markers = text.count("~~")
        if remaining_markers % 2 != 0:
            label = f" for part_number={part_number!r}" if part_number else ""
            print(
                f"WARNING: unclosed '~~' strikethrough marker detected{label}; "
                "remaining text classified without that marker consumed.",
                file=sys.stderr,
            )

    return text


# ---------------------------------------------------------------------------
# §6.3 — Negation engine
# ---------------------------------------------------------------------------

# N1 — \b(negator)\s+(?:additional|more|extra\s+)?KEYWORD\b
# N2 — \bmissing\s+KEYWORD\b
# N3 — \bKEYWORD\s+missing\b
# N4 — \bdo\s+not\s+(?:install|need|require|use|apply)\s+KEYWORD\b
#
# N2 and N3 use the literal word "missing", independent of the negators list.

def find_negated_spans(
    preprocessed_note: str,
    keyword: str,
    negator_patterns: list[tuple[str, re.Pattern[str]]],
) -> list[Span]:
    """Return all spans in preprocessed_note where keyword is negated.

    Pre: keyword and negators are already regex-escaped via _compile_taxonomy.
    Post: every returned span covers the substring the classifier MUST suppress.
    Raises: never.
    """
    spans: list[Span] = []
    escaped_kw = re.escape(keyword)
    flags = re.IGNORECASE

    # N1 — any global negator before the keyword, with optional filler words
    for _neg_literal, _neg_pat in negator_patterns:
        escaped_neg = re.escape(_neg_literal)
        n1 = re.compile(
            r"\b" + escaped_neg + r"\s+(?:additional|more|extra\s+)?" + escaped_kw + r"\b",
            flags,
        )
        for m in n1.finditer(preprocessed_note):
            spans.append(Span(m.start(), m.end()))

    # N2 — missing KEYWORD
    n2 = re.compile(r"\bmissing\s+" + escaped_kw + r"\b", flags)
    for m in n2.finditer(preprocessed_note):
        spans.append(Span(m.start(), m.end()))

    # N3 — KEYWORD missing
    n3 = re.compile(r"\b" + escaped_kw + r"\s+missing\b", flags)
    for m in n3.finditer(preprocessed_note):
        spans.append(Span(m.start(), m.end()))

    # N4 — do not (install|need|require|use|apply) KEYWORD
    n4 = re.compile(
        r"\bdo\s+not\s+(?:install|need|require|use|apply)\s+" + escaped_kw + r"\b",
        flags,
    )
    for m in n4.finditer(preprocessed_note):
        spans.append(Span(m.start(), m.end()))

    return spans


# ---------------------------------------------------------------------------
# §6.4 — Disqualifier engine
# ---------------------------------------------------------------------------


def find_disqualified_spans(
    preprocessed_note: str,
    rule: DisqualifierRule,
    keyword_pattern: re.Pattern[str],
    not_followed_by_pattern: re.Pattern[str],
) -> list[Span]:
    """Return spans of keyword occurrences that the disqualifier rule fires on.

    GAP DEFINITION: for each keyword match K and each not_followed_by match F
    whose F.start > K.end, gap = F.start - K.end (characters of preprocessed_note).
    The rule fires (disqualifies K) if gap <= max_gap_chars.

    Pre: rule fields are non-empty; patterns are pre-compiled.
    Post: returned spans are over preprocessed_note.
    Raises: never.
    """
    disqualified: list[Span] = []

    nfb_matches = list(not_followed_by_pattern.finditer(preprocessed_note))
    if not nfb_matches:
        return disqualified

    for kw_match in keyword_pattern.finditer(preprocessed_note):
        k_start, k_end = kw_match.start(), kw_match.end()
        for nfb_match in nfb_matches:
            f_start = nfb_match.start()
            if f_start > k_end:
                gap = f_start - k_end
                if gap <= rule.max_gap_chars:
                    disqualified.append(Span(k_start, k_end))
                    break  # one disqualifying NFB is enough to suppress this keyword occurrence

    return disqualified


# ---------------------------------------------------------------------------
# §6.5 — Classifier
# ---------------------------------------------------------------------------

_CONTEXT_WINDOW = 40  # characters on each side for context_excerpt


def _build_context_excerpt(note: str, span: Span) -> str:
    left = note[max(0, span.start - _CONTEXT_WINDOW) : span.start]
    match_text = note[span.start : span.end]
    right = note[span.end : span.end + _CONTEXT_WINDOW]
    return left + match_text + right


def _span_is_suppressed(span: Span, suppressed_spans: list[Span]) -> bool:
    """Return True if the match span overlaps any suppressed span."""
    for sup in suppressed_spans:
        # Overlap: not (span.end <= sup.start or span.start >= sup.end)
        if not (span.end <= sup.start or span.start >= sup.end):
            return True
    return False


def classify_note(
    preprocessed_note: str,
    compiled: _CompiledTaxonomy,
    part_number: str,
) -> dict:
    """Classify a single preprocessed note.

    Pre: preprocessed_note is non-empty; compiled was produced by load_taxonomy.
    Post: returns {
        "categories": Map[CategoryName, Set[KeywordLiteral]],
        "suppressions": List[Suppression],
    }
    AMBIGUITY RESOLUTION: when a keyword occurrence is simultaneously a positive
    match and a negation candidate, the engine resolves to NON-MATCH (false
    positives strictly worse than false negatives).
    Raises: never (provided compiled was produced by load_taxonomy).
    """
    categories: dict[str, set[str]] = {}
    suppressions: list[Suppression] = []

    for comp_cat in compiled.categories:
        cat_name = comp_cat.source.name

        # Collect all negated spans (across all keywords — negation is global within the note)
        negated_spans: list[Span] = []
        for kw, kw_pat in comp_cat.keyword_patterns.items():
            neg_spans = find_negated_spans(
                preprocessed_note, kw, compiled.negator_patterns
            )
            negated_spans.extend(neg_spans)

        # Collect disqualified spans (per rule)
        disqualified_spans: list[Span] = []
        for rule, kw_pat, nfb_pat in comp_cat.disqualifier_patterns:
            dq_spans = find_disqualified_spans(
                preprocessed_note, rule, kw_pat, nfb_pat
            )
            disqualified_spans.extend(dq_spans)

        matched_keywords: set[str] = set()

        for kw, kw_pat in comp_cat.keyword_patterns.items():
            for m in kw_pat.finditer(preprocessed_note):
                m_span = Span(m.start(), m.end())

                # Negation check — resolve to non-match (AMBIGUITY RESOLUTION)
                if _span_is_suppressed(m_span, negated_spans):
                    suppressions.append(
                        Suppression(
                            part_number=part_number,
                            category=cat_name,
                            keyword=kw,
                            reason="negation",
                            context_excerpt=_build_context_excerpt(preprocessed_note, m_span),
                        )
                    )
                    continue

                # Disqualifier check
                if _span_is_suppressed(m_span, disqualified_spans):
                    suppressions.append(
                        Suppression(
                            part_number=part_number,
                            category=cat_name,
                            keyword=kw,
                            reason="disqualifier",
                            context_excerpt=_build_context_excerpt(preprocessed_note, m_span),
                        )
                    )
                    continue

                matched_keywords.add(kw)

        if matched_keywords:
            categories[cat_name] = matched_keywords

    return {"categories": categories, "suppressions": suppressions}


# ---------------------------------------------------------------------------
# §6.7 — Output writer
# ---------------------------------------------------------------------------


def write_category_matches(
    matches: dict[str, list[MatchedPart]],
    output_path: Path,
    taxonomy_version: str,
) -> None:
    """Serialize CategoryMatches to output_path atomically.

    Pre: output_path's parent exists and is writable.
    Post: output_path contains valid JSON per §7.1 on success; unchanged on failure.
    Raises: OutputWriteFailed.
    """
    payload = {
        "schema_version": "1",
        "generated_at": _utcnow_z(),
        "taxonomy_version": taxonomy_version,
        "categories": {
            cat_name: [
                {
                    "part_number": mp.part_number,
                    "matched_keywords": list(mp.matched_keywords),
                }
                for mp in sorted(parts, key=lambda p: p.part_number)
            ]
            for cat_name, parts in matches.items()
        },
    }
    _atomic_write_json(payload, output_path)


def _atomic_write_json(payload: object, target: Path) -> None:
    """Write JSON atomically via tmp-file + fsync + rename."""
    parent = target.parent
    try:
        fd, tmp_path_str = tempfile.mkstemp(
            dir=str(parent), suffix=".tmp", prefix=target.name + "."
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
        except Exception:
            try:
                os.unlink(tmp_path_str)
            except OSError:
                pass
            raise
        os.replace(tmp_path_str, str(target))
    except OSError as exc:
        raise OutputWriteFailed(
            f"Atomic write to {target} failed: {exc}"
        ) from exc


def _utcnow_z() -> str:
    """Return current UTC time as ISO 8601 with Z suffix."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_filename_ts(ts: str) -> str:
    """Convert a Z-suffixed UTC timestamp to a filesystem-safe name component."""
    # "2026-05-13T14:32:01Z" → "2026-05-13T14-32-01Z"
    return ts.replace(":", "-")


# ---------------------------------------------------------------------------
# §6.6 — Orchestrator
# ---------------------------------------------------------------------------

_REQUIRED_COLUMNS = {"part_number", "base_mfg_notes"}


def run_extraction(
    db_path: Path,
    taxonomy_path: Path,
    output_dir: Path,
    busy_timeout_ms: int = 5000,
) -> RunReport:
    """End-to-end run: open DB → scan → classify → write JSON + run report.

    Pre: db_path exists; taxonomy_path is loadable; output_dir exists and is writable.
    Post: category_matches.json + timestamped run_report written atomically.
    Raises: DatabaseUnavailable, SchemaMismatch, OutputDirectoryMissing,
            OutputWriteFailed, ScanInterrupted.
    """
    if not output_dir.exists():
        raise OutputDirectoryMissing(
            f"output_dir does not exist: {output_dir.resolve()}\n"
            "Create the directory before invoking the classifier."
        )

    # Load taxonomy first so a bad YAML aborts before any DB work.
    compiled = load_taxonomy(taxonomy_path)
    taxonomy_version = _sha256_file(taxonomy_path)

    started_at = _utcnow_z()
    print(f"[classifier] started at {started_at}")
    print(f"[classifier] taxonomy: {taxonomy_path} ({taxonomy_version[:12]}...)")
    print(f"[classifier] db: {db_path}")

    if not db_path.exists():
        raise DatabaseUnavailable(f"DB file not found: {db_path.resolve()}")

    # Open connection with read-only defense.
    try:
        con = sqlite3.connect(str(db_path), timeout=busy_timeout_ms / 1000.0)
    except sqlite3.OperationalError as exc:
        raise DatabaseUnavailable(f"Cannot connect to {db_path}: {exc}") from exc

    try:
        con.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        con.execute("PRAGMA query_only = ON")

        # Verify schema.
        try:
            cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assemblies'")
            if cur.fetchone() is None:
                raise SchemaMismatch("Table 'assemblies' not found in database.")
            cur = con.execute("PRAGMA table_info(assemblies)")
            col_names = {row[1] for row in cur.fetchall()}
            missing = _REQUIRED_COLUMNS - col_names
            if missing:
                raise SchemaMismatch(
                    f"assemblies table is missing columns: {', '.join(sorted(missing))}"
                )
        except sqlite3.OperationalError as exc:
            raise SchemaMismatch(f"Schema validation failed: {exc}") from exc

        # Accumulate results.
        category_matches: dict[str, dict[str, set[str]]] = {
            cat.source.name: {} for cat in compiled.categories
        }
        all_suppressions: list[Suppression] = []
        rows_scanned = 0
        rows_skipped = 0
        rows_with_error = 0

        try:
            cursor = con.execute(
                "SELECT part_number, base_mfg_notes "
                "FROM assemblies "
                "WHERE base_mfg_notes IS NOT NULL AND base_mfg_notes != ''"
            )
        except sqlite3.OperationalError as exc:
            raise ScanInterrupted(f"Failed to start DB scan: {exc}") from exc

        try:
            for row in cursor:
                part_number: str = row[0]
                raw_note: str | None = row[1]

                if not raw_note or not raw_note.strip():
                    rows_skipped += 1
                    continue

                preprocessed = preprocess_note(
                    raw_note,
                    compiled.source.strip_strikethrough,
                    compiled,
                    part_number=part_number,
                )

                if not preprocessed.strip():
                    rows_skipped += 1
                    continue

                rows_scanned += 1

                # Per-row defensive wrap (§6.5 — orchestrator must catch classifier errors)
                try:
                    result = classify_note(preprocessed, compiled, part_number)
                except Exception as exc:  # noqa: BLE001
                    rows_with_error += 1
                    print(
                        f"WARNING: classifier error for part_number={part_number!r}: {exc}",
                        file=sys.stderr,
                    )
                    continue

                for cat_name, keywords in result["categories"].items():
                    existing = category_matches.get(cat_name)
                    if existing is None:
                        continue
                    if part_number not in existing:
                        existing[part_number] = set()
                    existing[part_number].update(keywords)

                all_suppressions.extend(result["suppressions"])

        except sqlite3.OperationalError as exc:
            raise ScanInterrupted(
                f"DB I/O error during scan (rows_scanned so far: {rows_scanned}): {exc}"
            ) from exc

    finally:
        con.close()

    print(f"[classifier] rows_scanned={rows_scanned}, skipped={rows_skipped}, errors={rows_with_error}")

    # Build final CategoryMatches structure.
    final_matches: dict[str, list[MatchedPart]] = {}
    per_category_counts: dict[str, int] = {}
    for cat_name, part_map in category_matches.items():
        parts = [
            MatchedPart(
                part_number=pn,
                matched_keywords=tuple(sorted(kws)),
            )
            for pn, kws in part_map.items()
        ]
        parts.sort(key=lambda p: p.part_number)
        final_matches[cat_name] = parts
        per_category_counts[cat_name] = len(parts)

    # Write category_matches.json atomically.
    matches_path = output_dir / "category_matches.json"
    write_category_matches(final_matches, matches_path, taxonomy_version)
    print(f"[classifier] wrote {matches_path}")

    finished_at = _utcnow_z()

    report = RunReport(
        started_at=started_at,
        finished_at=finished_at,
        db_path=str(db_path.resolve()),
        taxonomy_version=taxonomy_version,
        rows_scanned=rows_scanned,
        rows_skipped_null_notes=rows_skipped,
        rows_with_classifier_error=rows_with_error,
        suppressions=all_suppressions,
        per_category_counts=per_category_counts,
    )

    _write_run_report(report, output_dir)
    return report


def _write_run_report(report: RunReport, output_dir: Path) -> None:
    """Write a timestamped run report and update the run_report-latest.json pointer."""
    safe_ts = _safe_filename_ts(report.started_at)
    report_filename = f"run_report-{safe_ts}.json"
    report_path = output_dir / report_filename

    report_payload = {
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "db_path": report.db_path,
        "taxonomy_version": report.taxonomy_version,
        "rows_scanned": report.rows_scanned,
        "rows_skipped_null_notes": report.rows_skipped_null_notes,
        "rows_with_classifier_error": report.rows_with_classifier_error,
        "suppressions": [
            {
                "part_number": s.part_number,
                "category": s.category,
                "keyword": s.keyword,
                "reason": s.reason,
                "context_excerpt": s.context_excerpt,
            }
            for s in report.suppressions
        ],
        "per_category_counts": report.per_category_counts,
    }
    _atomic_write_json(report_payload, report_path)
    print(f"[classifier] wrote {report_path}")

    # Write run_report-latest.json — use symlink on POSIX; JSON pointer on Windows.
    latest_path = output_dir / "run_report-latest.json"
    _write_latest_pointer(report_filename, latest_path)


def _write_latest_pointer(report_filename: str, latest_path: Path) -> None:
    """Write run_report-latest.json as a symlink (POSIX) or JSON pointer (Windows)."""
    # Try symlink first; fall back to pointer document.
    try:
        if latest_path.exists() or latest_path.is_symlink():
            latest_path.unlink()
        latest_path.symlink_to(report_filename)
    except (OSError, NotImplementedError):
        # Windows without developer mode symlinks; write a pointer document.
        _atomic_write_json({"points_to": report_filename}, latest_path)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

_DEFAULT_DB = Path(r"D:\Dev\Scheduler\Schedule\dist\Scheduler\schedule.db")
_DEFAULT_TAXONOMY = Path(__file__).parent / "taxonomy.yaml"
_DEFAULT_OUTPUT_DIR = Path(__file__).parent


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="2nd/3rd OP keyword classifier for assembly notes.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=_DEFAULT_DB,
        help="Path to schedule.db (SQLite).",
    )
    parser.add_argument(
        "--taxonomy",
        type=Path,
        default=_DEFAULT_TAXONOMY,
        help="Path to taxonomy YAML file.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_DEFAULT_OUTPUT_DIR,
        help="Directory for category_matches.json and run reports. Must exist.",
    )
    parser.add_argument(
        "--busy-timeout",
        type=int,
        default=5000,
        metavar="MS",
        help="SQLite busy_timeout in milliseconds.",
    )
    return parser


def main() -> None:
    parser = _build_arg_parser()
    args = parser.parse_args()

    try:
        report = run_extraction(
            db_path=args.db,
            taxonomy_path=args.taxonomy,
            output_dir=args.output_dir,
            busy_timeout_ms=args.busy_timeout,
        )
    except (
        TaxonomyNotFound,
        TaxonomyMalformed,
        TaxonomyInvalid,
        DatabaseUnavailable,
        SchemaMismatch,
        OutputDirectoryMissing,
        OutputWriteFailed,
        ScanInterrupted,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(
        f"[classifier] done — "
        f"Adhesives={report.per_category_counts.get('Adhesives', 0)}, "
        f"Hardware={report.per_category_counts.get('Hardware', 0)}, "
        f"Conductive={report.per_category_counts.get('Conductive', 0)}, "
        f"Torque={report.per_category_counts.get('Torque', 0)}"
    )


if __name__ == "__main__":
    main()
