"""
Text and value normalization utilities.

Kept deliberately simple and deterministic (no ML) so behavior is
predictable and auditable. Anything fuzzier belongs in the matching layer.
"""
import math
import re
import unicodedata
from typing import Any, Optional


_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9а-яё\s]", re.IGNORECASE)

# Common unit / abbreviation normalizations (extend as needed)
_UNIT_REPLACEMENTS = {
    r"\bmm\b": "mm",
    r"\bcm\b": "cm",
    r"\bkg\b": "kg",
    r"\bpcs?\b": "pcs",
    r"\bpc\b": "pcs",
}


def is_missing(value: Any) -> bool:
    """True for None, pandas/numpy NaN, and common empty Excel placeholders."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    text = str(value).strip().lower()
    return text in ("", "nan", "none", "n/a", "-", "<na>", "nat")


def clean_text(value: Optional[str]) -> str:
    """Lowercase, strip accents/punctuation noise, collapse whitespace."""
    if is_missing(value):
        return ""
    text = str(value).strip()
    if text.lower() in ("nan", "none", "n/a", "-"):
        return ""

    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    text = _NON_ALNUM_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()

    for pattern, replacement in _UNIT_REPLACEMENTS.items():
        text = re.sub(pattern, replacement, text)

    return text


def clean_code(value: Any) -> str:
    """Normalize an item/government code: uppercase, strip whitespace."""
    if is_missing(value):
        return ""
    # Excel often stores numeric codes as floats (12345.0)
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if value == int(value):
            value = int(value)
    text = str(value).strip().upper()
    if text.lower() in ("nan", "none", ""):
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def to_float(value) -> Optional[float]:
    if is_missing(value):
        return None
    try:
        result = float(str(value).replace(",", "").replace(" ", ""))
        if math.isnan(result):
            return None
        return result
    except (ValueError, TypeError):
        return None


def build_normalized_text(*parts: Optional[str]) -> str:
    """Concatenate several fields into one normalized bag-of-words string."""
    cleaned = [clean_text(p) for p in parts if p]
    return " ".join(c for c in cleaned if c)
