"""Attribute extraction / standardisation (HANDOFF.md section 5, Task 2).

Why this exists: the v2 matching engine (see UPGRADE_V2.md) treats a
product name as one blob of text. That is enough to tell "Манеж детский"
apart from "Корзина для игрушек", but not enough to tell these three apart:

    Манеж детский размерами 830х680 мм
    Манеж детский размерами 840х840х680 мм
    Манеж детский размерами 840х840х680 мм (усиленный)

`normalizer.py` already unifies "830*680" / "830х680" / "830x680" into one
separator so they at least tokenize consistently, but the numbers are still
just tokens in a text blob - nothing compares 830 against 840 as numbers.
This module parses them into actual columns (see models.py: dim_w_mm,
dim_h_mm, dim_d_mm, material, unit_normalized, quantity_normalized) so a
later scoring signal can compare "is 830 close to 840?" instead of "do these
two strings share any characters?".

--- Deliberately additive, deliberately heuristic ------------------------

This is regex/keyword-based, not ML, for the same reason column_mapper.py
is alias-based: it needs to be fast and deterministic over a 5,163-row
catalog, and auditable - a wrong parse should be traceable to one regex, not
buried in a model. It is also allowed to return None liberally: a missing
attribute is just an honest "unknown", the same NULL-over-guess principle
`models.py` documents for `matches.catalog_version_id`. Nothing here ever
touches `product_name`, `description`, or `raw_data` - see ingestion.py.

--- What this module does NOT do (yet) ------------------------------------

It does not feed into `scoring.py`. Adding a fifth signal there requires
re-normalizing `ScoringWeights` (asserted to sum to 1.0) and re-running the
calibration benchmark HANDOFF.md section 6 describes - skipping that step
is exactly how the original 0.75-score-ceiling bug happened. That is a
separate, deliberately deferred step once real score-distribution numbers
are available; this module only produces the columns that step will need.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.normalizer import normalize_text

# --- Dimensions --------------------------------------------------------
#
# Operates on `normalize_text(...)` output, not raw text: normalizer.py
# already unifies "900*900", "900х900" (Cyrillic х), "900×900" (multiplication
# sign) into a single literal "900x900" form before this ever sees it, so one
# pattern covers every separator variant found in the real files.
_DIM_RE = re.compile(r"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(?:x(\d+(?:\.\d+)?))?")

# Unit immediately following a dimension match, e.g. "830x680 мм". Only мм/см
# are handled - the real files do not use other units for dimensions, and
# guessing at an unfamiliar unit is worse than leaving it unscaled (mm is the
# overwhelmingly common case, so no-unit defaults to mm rather than raising).
_CM_TO_MM = 10.0


@dataclass(frozen=True)
class Dimensions:
    width_mm: float | None = None
    height_mm: float | None = None
    depth_mm: float | None = None


def extract_dimensions(*texts: str | None) -> Dimensions:
    """Parses width/height[/depth] in millimeters out of the first text
    that contains a recognizable "NxN" or "NxNxN" pattern.

    Multiple texts can be given in priority order (e.g. a dedicated
    "Размеры"/"Габариты" column first, product_name second) - the first
    match wins, so a real, structured dimensions field is always preferred
    over parsing it back out of free text.
    """
    for text in texts:
        if not text:
            continue
        normalized = normalize_text(text)
        match = _DIM_RE.search(normalized)
        if not match:
            continue

        tail = normalized[match.end():match.end() + 6].strip()
        next_word = tail.split(" ", 1)[0] if tail else ""
        scale = _CM_TO_MM if next_word == "см" else 1.0

        def _mm(value: str | None) -> float | None:
            if value is None:
                return None
            return round(float(value) * scale, 2)

        return Dimensions(
            width_mm=_mm(match.group(1)),
            height_mm=_mm(match.group(2)),
            depth_mm=_mm(match.group(3)),
        )

    return Dimensions()


# --- Material ------------------------------------------------------------
#
# Ordered so that a more specific compound term is checked before the
# generic term it contains as a substring - e.g. "лдсп" before "дсп",
# "нержавеющая сталь" before the bare "сталь" it would otherwise match.
# Keys are stems (normalize_text lowercases and strips punctuation, but not
# word endings), so "деревянный"/"дерева"/"дерево" all match "дерев".
_MATERIAL_STEMS: list[tuple[str, str]] = [
    ("лдсп", "лдсп"),
    ("дсп", "дсп"),
    ("мдф", "мдф"),
    ("фанер", "фанера"),
    ("дерев", "дерево"),
    ("нержавею", "нержавеющая сталь"),
    ("металл", "металл"),
    ("сталь", "сталь"),
    ("пластик", "пластик"),
    ("поролон", "поролон"),
    ("резин", "резина"),
    ("текстил", "текстиль"),
    ("ткан", "ткань"),
    ("стекл", "стекло"),
    ("кож", "кожа"),
]


def extract_material(*texts: str | None) -> str | None:
    """Returns a canonical material name if any known stem appears in any of
    the given texts (checked in order), else None - an unrecognized or
    absent material is left unknown rather than guessed.
    """
    for text in texts:
        if not text:
            continue
        normalized = normalize_text(text)
        for stem, canonical in _MATERIAL_STEMS:
            if stem in normalized:
                return canonical
    return None


# --- Unit ------------------------------------------------------------------
#
# Maps common spellings/abbreviations of the same unit to one canonical
# token, so "шт", "шт.", "штук", "штука" compare equal instead of as four
# different strings. Deliberately small: an unrecognized unit is passed
# through normalized-but-as-is rather than dropped, since "unknown but
# consistent" is still useful for an exact-unit comparison later.
_UNIT_ALIASES: dict[str, str] = {
    "шт": "шт", "штук": "шт", "штука": "шт",
    "компл": "компл", "комплект": "компл", "к т": "компл",
    "набор": "компл",
    "уп": "уп", "упаковка": "уп",
    "пач": "пач", "пачка": "пач",
    "пог м": "пог_м", "погонный метр": "пог_м",
    "кв м": "м2", "м2": "м2",
    "м": "м", "метр": "м",
    "кг": "кг", "г": "г", "л": "л", "мл": "мл",
}


def normalize_unit(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = normalize_text(text)
    if not cleaned:
        return None
    return _UNIT_ALIASES.get(cleaned, cleaned)


@dataclass(frozen=True)
class ExtractedAttributes:
    dim_w_mm: float | None = None
    dim_h_mm: float | None = None
    dim_d_mm: float | None = None
    material: str | None = None
    unit_normalized: str | None = None


def extract_attributes(
    product_name: str | None,
    description: str | None = None,
    *,
    raw_dimensions: str | None = None,
    raw_unit: str | None = None,
) -> ExtractedAttributes:
    """Convenience wrapper used by ingestion.py: runs every extractor with
    the right text priority for each field.

    `raw_dimensions` is the "Размеры"/"Габариты" column when the source
    sheet has one (see column_mapper.CANONICAL_FIELD_ALIASES["dimensions"])
    - preferred over parsing dimensions back out of the name/description,
    since a dedicated column is a more direct source than free text.
    """
    dims = extract_dimensions(raw_dimensions, product_name, description)
    material = extract_material(product_name, description)
    unit_normalized = normalize_unit(raw_unit)

    return ExtractedAttributes(
        dim_w_mm=dims.width_mm,
        dim_h_mm=dims.height_mm,
        dim_d_mm=dims.depth_mm,
        material=material,
        unit_normalized=unit_normalized,
    )
