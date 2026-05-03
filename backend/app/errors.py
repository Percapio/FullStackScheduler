"""Resolver mapping ``processing_error`` substrings to highlight field names.

This is a leaf module by design: it imports nothing from the rest of the
project so that both ``schemas.py`` (which surfaces ``highlight_fields`` as a
computed field) and ``services/staging.py`` can depend on it without forming
an import cycle.
"""
from __future__ import annotations


HIGHLIGHT_RULES: list[tuple[str, list[str]]] = [
    ("Invalid JOB cell",                                    ["raw_job"]),
    ("Multiple build qualifiers in JOB cell",               ["raw_job"]),
    ("JOB cell appears to contain multiple part numbers",   ["raw_job"]),  # R3
    ("SO# is not allowed in JOB cell",                      ["raw_job"]),  # R4
    ("Invalid QTY",                                         ["raw_qty"]),
    ("raw_customer is empty",                               ["raw_customer"]),
    ("split_suffix overflow",                               ["raw_job"]),
    ("repeat_reference overflow",                           ["raw_job"]),
    ("Unparseable SHIPPED date",                            ["raw_shipped"]),
    ("Conflict: Attempting to update a shipped job",        ["raw_job"]),
    ("Intra-file duplicate JOB identity",                   ["raw_job"]),
]


def resolve_highlight_fields(processing_error: str | None) -> list[str]:
    """Return the raw-field keys to highlight for a given ``processing_error``.

    Returns an empty list when ``processing_error`` is falsy or matches no
    rule. Always returns a fresh list so callers cannot mutate shared state.
    """
    if not processing_error:
        return []
    for needle, fields in HIGHLIGHT_RULES:
        if needle in processing_error:
            return list(fields)
    return []
