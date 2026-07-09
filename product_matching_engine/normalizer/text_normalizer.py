"""
text_normalizer.py

Turns raw, messy spreadsheet text into a consistent form so that both the
embedding model and the heuristic re-ranker are comparing like with like.

Normalization steps:
    1. Lowercase
    2. Standardize units (mm, cm, m, kg, g, l, ml, w, v, a, etc.) so that
       "220V", "220 v", "220volt" all collapse to "220v"
    3. Strip punctuation (keeping alphanumerics and unit-relevant characters)
    4. Collapse repeated whitespace, strip leading/trailing whitespace

This module has no dependency on pandas/Excel — it operates purely on
strings so it can be unit tested and reused by any future importer.
"""

from __future__ import annotations

import re
from typing import Iterable

# Order matters: longer/compound unit spellings must be matched before their
# abbreviations to avoid partial replacement bugs.
_UNIT_PATTERNS: list[tuple[str, str]] = [
    (r"\bmillimeters?\b", "mm"),
    (r"\bmillimetres?\b", "mm"),
    (r"\bcentimeters?\b", "cm"),
    (r"\bcentimetres?\b", "cm"),
    (r"\bmeters?\b", "m"),
    (r"\bmetres?\b", "m"),
    (r"\bkilograms?\b", "kg"),
    (r"\bgrams?\b", "g"),
    (r"\bliters?\b", "l"),
    (r"\blitres?\b", "l"),
    (r"\bmilliliters?\b", "ml"),
    (r"\bmillilitres?\b", "ml"),
    (r"\bwatts?\b", "w"),
    (r"\bvolts?\b", "v"),
    (r"\bamperes?\b", "a"),
    (r"\bamps?\b", "a"),
    (r"\bhertz\b", "hz"),
    (r"\binches?\b", "in"),
]

# Collapse "220 v", "220-v", "220v" -> "220v" for common unit suffixes.
_UNIT_SPACING_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*[-]?\s*(mm|cm|m|kg|g|ml|l|w|v|a|hz|in)\b"
)

_PUNCT_RE = re.compile(r"[^\w\s.]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def standardize_units(text: str) -> str:
    """Expand unit words to abbreviations and tighten "<number> <unit>" gaps."""
    for pattern, replacement in _UNIT_PATTERNS:
        text = re.sub(pattern, replacement, text)
    text = _UNIT_SPACING_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}", text)
    return text


def normalize_text(text: str | None) -> str:
    """Full normalization pipeline applied to a single text field.

    Returns an empty string for None/NaN-like input rather than raising, so
    callers can safely map this over a whole DataFrame column.
    """
    if text is None:
        return ""
    text = str(text)
    if text.strip().lower() in {"nan", "none"}:
        return ""

    text = text.lower()
    text = standardize_units(text)
    text = _PUNCT_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def build_search_text(*fields: Iterable[str]) -> str:
    """Concatenate several already-normalized fields into one blob used for
    embedding / candidate retrieval. Empty fields are skipped so we don't
    introduce extra whitespace noise.
    """
    parts = [f for f in fields if f]
    return " ".join(parts).strip()
