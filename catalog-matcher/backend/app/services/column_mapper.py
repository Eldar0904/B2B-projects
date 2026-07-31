"""Column mapping layer.

Excel files coming from different sources name their columns differently
(Наименование / Название / Товар / Product Name / ...). This module maps
whatever headers are present in a given sheet onto a fixed set of canonical
fields the rest of the pipeline understands, per spec section 5.

Mapping is alias-dictionary + substring based (not ML), so it is fast and
deterministic on very large sheets. If automatic detection can't confidently
map a canonical field, it is left unmapped and the caller (API layer) can
ask the user to supply a manual override — see `apply_manual_overrides`.
"""

from __future__ import annotations

import re

# canonical_field -> list of header aliases (already lowercase, ё normalized to е)
CANONICAL_FIELD_ALIASES: dict[str, list[str]] = {
    "external_id": [
        "код",
        "code",
        "sku",
        "артикул",
        "id",
        "manufacturer code",
        "код товара",
    ],
    "product_name": [
        "наименование товара",
        "наименование",
        "название товара",
        "название",
        "товар",
        "product name",
        "name",
        "product",
    ],
    "description": [
        "описание",
        "description",
        "характеристики",
        "спецификация",
    ],
    "unit": [
        "единица измерения",
        "ед изм",
        "ед. изм.",
        "единица",
        "unit",
        "uom",
    ],
    "price": [
        "цена с ндс в тенге",
        "цена с ндс",
        "сметная цена тенге",
        "сметная цена",
        "цена тенге",
        "цена",
        "price",
        "стоимость",
    ],
    "quantity": [
        "sum из кол во",
        "кол во",
        "количество",
        "quantity",
        "qty",
    ],
    "freight_class": [
        "класс груза",
    ],
    "gross_weight_kg": [
        "масса брутто кг",
        "масса брутто",
        "вес брутто",
    ],
    "search_text": [
        "поисковый текст",
        "поисковая маска",
    ],
    "category": [
        "категория",
        "category",
    ],
    "brand": [
        "бренд",
        "brand",
        "производитель",
    ],
    "model": [
        "модель",
        "model",
    ],
    "dimensions": [
        "размеры",
        "габариты",
        "dimensions",
    ],
}

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[.,;:()\[\]«»\"' /\\-]+")


def normalize_header(header: str) -> str:
    """Lowercase, normalize ё->е, strip punctuation, collapse whitespace."""
    if header is None:
        return ""
    h = str(header).strip().lower()
    h = h.replace(" ", " ")
    h = h.replace("ё", "е")
    h = _PUNCT_RE.sub(" ", h)
    h = _WS_RE.sub(" ", h).strip()
    return h


def _alias_matches(normalized_header: str, normalized_alias: str) -> bool:
    if not normalized_header or not normalized_alias:
        return False
    if normalized_header == normalized_alias:
        return True
    # substring match either direction, but require alias to be a whole-word
    # sequence within the header (avoids "id" matching inside an unrelated word)
    pattern = r"(?:^|\s)" + re.escape(normalized_alias) + r"(?:$|\s)"
    if re.search(pattern, normalized_header):
        return True
    pattern2 = r"(?:^|\s)" + re.escape(normalized_header) + r"(?:$|\s)"
    if re.search(pattern2, normalized_alias):
        return True
    return False


def auto_map_columns(headers: list[str]) -> tuple[dict[str, str], list[str]]:
    """Map raw Excel headers to canonical field names.

    Returns (mapping, unmapped_headers) where mapping is
    {canonical_field: original_header}. Only fields that found a confident
    match are included. Each original header is used at most once, and the
    longest matching alias wins when multiple fields could plausibly claim
    the same header.
    """
    normalized_headers = {h: normalize_header(h) for h in headers}
    claimed_headers: set[str] = set()
    mapping: dict[str, str] = {}

    # Build all candidate (field, header, alias_len) matches, then greedily
    # assign by longest alias match first so more specific aliases win over
    # generic ones (e.g. "наименование товара" over "наименование").
    candidates: list[tuple[int, str, str]] = []
    for field, aliases in CANONICAL_FIELD_ALIASES.items():
        for header, norm_header in normalized_headers.items():
            for alias in aliases:
                if _alias_matches(norm_header, alias):
                    candidates.append((len(alias), field, header))

    candidates.sort(key=lambda c: c[0], reverse=True)

    claimed_fields: set[str] = set()
    for _alias_len, field, header in candidates:
        if field in claimed_fields or header in claimed_headers:
            continue
        mapping[field] = header
        claimed_fields.add(field)
        claimed_headers.add(header)

    unmapped = [h for h in headers if h not in claimed_headers]
    return mapping, unmapped


def apply_manual_overrides(
    mapping: dict[str, str], overrides: dict[str, str] | None
) -> dict[str, str]:
    """Merge a user-supplied manual mapping on top of the auto-detected one.

    `overrides` is {canonical_field: original_header}, as would come from a
    UI where the user manually maps a column that automatic detection missed
    or got wrong.
    """
    if not overrides:
        return mapping
    result = dict(mapping)
    result.update(overrides)
    return result
