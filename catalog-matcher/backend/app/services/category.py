"""
Category extraction and inference for government catalog codes.

Government codes are hierarchical, e.g. 521-201-0501:
  521-201-0500 -> section header (Предмет «Музыка»)
  521-201-0501 -> product row (Пианино)

We use the section level (…-0500) as category_code for matching — the
two-segment prefix (521-201) is too broad and mixes unrelated sections.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from rapidfuzz import fuzz, process

from app.models.db_models import CatalogProduct, InternalItem

# Keyword hints mapped to catalog section codes (category_code).
CATEGORY_KEYWORD_HINTS: Dict[str, List[str]] = {
    "521-201-0500": [
        "рояль", "пианино", "фортепиано", "гитар", "скрипк", "виолончел",
        "флейт", "саксофон", "барабан", "музыкальн", "укулеле", "струнн",
        "смычков", "клавиш", "орган", "аккордеон", "баян", "синтезатор",
        "труба", "тромбон", "кларнет", "корнет",
    ],
    "521-105-0500": [
        "домбр", "кобыз", "жетіген", "jetigen", "саз", "канон",
        "национальн музык",
    ],
    "521-402-0100": [
        "физкульт", "гимнаст", "лечебн", "тренаж", "гантел", "штанг", "гриф",
        "мат напольн", "мат борцов", "батут", "скакалк", "обруч", "мяч гимнаст",
    ],
    "521-301-0300": [
        "математ", "геометр", "штангенциркул", "циркуль", "транспортир",
    ],
    "521-101-0100": [
        "игров", "кукольн", "манеж", "песочниц", "горк",
    ],
    "522-103-0100": [
        "стол", "стул", "шкаф", "стеллаж", "тумб",
    ],
    "521-208-0100": [
        "мебель", "изо", "ателье", "табурет", "стул", "культ", "трудов",
    ],
    "521-102-0100": [
        "изо", "рисован", "краск", "кист", "мольберт", "холст",
    ],
}

# User Excel category labels (Мебель, Техника, …) -> government section codes.
USER_CATEGORY_LABELS: Dict[str, List[str]] = {
    "521-208-0100": ["мебель", "изо", "ателье", "интерьер", "диван", "кресл", "стул", "шкаф"],
    "522-103-0100": ["мебель", "стол", "стеллаж", "тумб"],
    "521-101-0400": ["техника", "компьютер", "оргтехника", "it", "пк", "моноблок", "мфу", "принтер"],
    "522-101-0200": ["электро", "лаборатор", "питани"],
}


def extract_category_code(code: Optional[str]) -> Optional[str]:
    """
    Return section-level category code, e.g. 521-201-0501 -> 521-201-0500.
    Falls back to the first two segments when the code is shorter.
    """
    parts = [p for p in (code or "").strip().split("-") if p]
    if len(parts) < 2:
        return None
    if len(parts) < 3 or not parts[2].isdigit():
        return f"{parts[0]}-{parts[1]}"

    third = parts[2]
    section_third = f"{(int(third) // 100) * 100:0{len(third)}d}"
    return f"{parts[0]}-{parts[1]}-{section_third}"


def is_category_header_code(code: Optional[str]) -> bool:
    """True for section header rows like 521-201-0500."""
    parts = [p for p in (code or "").strip().split("-") if p]
    if len(parts) < 3:
        return False
    third = parts[2]
    return third.isdigit() and third.endswith("00") and third not in ("000", "0000")


def looks_like_catalog_code(code: Optional[str]) -> bool:
    """True for government-style codes (521-101-0420), not internal labels (T6, M12)."""
    text = (code or "").strip()
    if not text:
        return False
    parts = [p for p in text.split("-") if p]
    if len(parts) < 2:
        return False
    return any(p.isdigit() for p in parts)


def build_category_name_map(products: List[CatalogProduct]) -> Dict[str, str]:
    """Build {category_code: category_name} from section header rows."""
    names: Dict[str, str] = {}

    for p in products:
        code = (p.code or "").strip()
        if not is_category_header_code(code):
            continue
        cat_code = extract_category_code(code)
        name = (p.name or "").strip()
        if cat_code and name:
            names[cat_code] = name

    # Fallback: first product name per section when no header row exists
    for p in products:
        cat_code = extract_category_code(p.code)
        if not cat_code or cat_code in names:
            continue
        name = (p.name or "").strip()
        if name:
            names[cat_code] = name

    return names


def apply_catalog_categories(products: List[CatalogProduct]) -> int:
    """Fill category_code / category_name on catalog products in-place."""
    name_map = build_category_name_map(products)
    updated = 0
    for p in products:
        cat_code = extract_category_code(p.code)
        cat_name = name_map.get(cat_code) if cat_code else None
        if p.category_code != cat_code or p.category_name != cat_name:
            p.category_code = cat_code
            p.category_name = cat_name
            updated += 1
    return updated


def backfill_source_categories(db, source_id: int) -> int:
    """Recompute categories for all products of a catalog source."""
    products = (
        db.query(CatalogProduct)
        .filter(CatalogProduct.source_id == source_id)
        .all()
    )
    count = apply_catalog_categories(products)
    db.commit()
    return count


def infer_item_category(
    item_name: Optional[str],
    description: Optional[str],
    category_name_map: Dict[str, str],
) -> Tuple[Optional[str], Optional[str], str]:
    """
    Guess category for an internal item without an explicit category column.

    Returns (category_code, category_name, explanation_suffix).
    """
    text = " ".join(
        p for p in [(item_name or "").lower(), (description or "").lower()] if p
    ).strip()
    if not text:
        return None, None, ""

    scores: Dict[str, int] = {}
    for cat_code, hints in CATEGORY_KEYWORD_HINTS.items():
        for hint in hints:
            if hint in text:
                scores[cat_code] = scores.get(cat_code, 0) + 1

    if scores:
        best_code = max(scores, key=scores.get)
        best_name = category_name_map.get(best_code)
        return best_code, best_name, f"category inferred ({best_code})"

    if category_name_map:
        choices = {code: name for code, name in category_name_map.items() if name}
        hit = process.extractOne(
            text,
            choices,
            scorer=fuzz.partial_ratio,
            score_cutoff=72,
        )
        if hit:
            _name, score, cat_code = hit
            return (
                cat_code,
                category_name_map.get(cat_code),
                f"category inferred by name match ({score:.0f}%)",
            )

    return None, None, ""


def resolve_category_from_label(
    category_name: Optional[str],
    category_name_map: Dict[str, str],
) -> Tuple[Optional[str], Optional[str], str]:
    """Map user-facing Excel category label to a government section code."""
    text = (category_name or "").strip().lower()
    if not text or text in ("прочее", "другое", "other", "разное"):
        return None, None, ""

    for cat_code, labels in USER_CATEGORY_LABELS.items():
        for label in labels:
            if label in text:
                return (
                    cat_code,
                    category_name_map.get(cat_code) or category_name,
                    f"category from label ({category_name})",
                )
    return None, None, ""


def resolve_item_category(
    item: InternalItem,
    category_name_map: Dict[str, str],
    infer_if_missing: bool = True,
) -> Tuple[Optional[str], Optional[str], str]:
    """Use explicit item category or infer when allowed."""
    if item.category_code:
        name = item.category_name or category_name_map.get(item.category_code)
        return item.category_code, name, "category from item"

    code, name, note = resolve_category_from_label(item.category_name, category_name_map)
    if code:
        return code, name, note

    if not infer_if_missing:
        return None, None, ""

    code, name, note = infer_item_category(
        item.item_name, item.description, category_name_map
    )
    return code, name, note


def product_ids_for_category(
    products: List[CatalogProduct],
    category_code: Optional[str],
) -> Optional[set]:
    """Return allowed product ids for a category, or None if unrestricted."""
    if not category_code:
        return None
    allowed = {p.id for p in products if p.category_code == category_code}
    return allowed if allowed else None
