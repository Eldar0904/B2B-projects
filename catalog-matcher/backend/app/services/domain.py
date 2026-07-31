"""
Domain rules for medical / healthcare items.

School-equipment catalogs often mention «шприц» inside physics-kit
descriptions — those must never match a real «Шприцы одноразовые» line item.
"""
from __future__ import annotations

from typing import List, Set

from app.matching.base import Candidate
from app.matching.name_match import anchor_overlap, significant_tokens
from app.models.db_models import CatalogProduct

# Item text contains any of these → healthcare supply, not school equipment.
MEDICAL_ITEM_KEYWORDS: List[str] = [
    "шприц", "грелк", "жгут", "катетер", "бинт", "шина", "стетоскоп",
    "тонометр", "небулайзер", "ингалятор", "физиотерап", "дарсонвал",
    "гальваниза", "магнитотерап", "ультразвук", "электрофорез", "аппарат для",
    "медицин", "кислородн", "перчатк мед", "маска мед", "халат",
]

# Must appear as a catalog product NAME token (not buried in a physics-kit title).
MEDICAL_PRODUCT_NAME_TOKENS: Set[str] = {
    "шприц", "шприцы", "грелка", "грелки", "жгут", "жгуты",
    "катетер", "катетеры", "бинт", "бинты", "шина", "шины",
    "стетоскоп", "тонометр", "небулайзер", "ингалятор", "кислородный",
    "перчатки", "шприцов", "игла", "иглы", "канюля", "система",
    "венозный", "внутривенный",
}

# School catalog sections that are never valid targets for healthcare items.
NON_MEDICAL_SECTIONS: Set[str] = {
    "522-101-1000",  # gym / balls / bars
    "521-402-0100",  # gym hall
    "521-301-0300",  # math cabinet
    "521-301-0500",  # physics demo kits
    "521-301-1600",  # biology lab glassware kits
    "521-202-0600",  # elementary mechanics kits
    "522-101-1000",  # technical teaching aids (boards, balls, …)
}


def item_text(item_name: str = "", description: str = "") -> str:
    return f"{item_name or ''} {description or ''}".lower()


def is_medical_item(item_name: str = "", description: str = "") -> bool:
    text = item_text(item_name, description)
    return any(kw in text for kw in MEDICAL_ITEM_KEYWORDS)


def _product_name_tokens(product: CatalogProduct) -> Set[str]:
    name = (product.name or "").lower()
    return set(significant_tokens(name, limit=12)) | set(name.split())


def catalog_product_is_medical(product: CatalogProduct) -> bool:
    """True only when the catalog row NAME is a healthcare product."""
    tokens = _product_name_tokens(product)
    return bool(tokens & MEDICAL_PRODUCT_NAME_TOKENS)


def blocked_product_ids_for_item(
    item_name: str,
    description: str,
    products: List[CatalogProduct],
) -> Set[int]:
    """Products that must not be suggested for this item."""
    if not is_medical_item(item_name, description):
        return set()

    blocked: Set[int] = set()
    for p in products:
        if p.category_code in NON_MEDICAL_SECTIONS:
            blocked.add(p.id)
            continue
        if not catalog_product_is_medical(p):
            blocked.add(p.id)
    return blocked


def filter_medical_candidates(
    item_name: str,
    description: str,
    candidates: List[Candidate],
    product_lookup: dict,
) -> List[Candidate]:
    """
    For healthcare line items, keep only candidates whose product NAME
    is a medical product and shares keywords with the item.
    """
    if not is_medical_item(item_name, description):
        return candidates

    item_label = item_name or ""
    kept: List[Candidate] = []
    for c in candidates:
        product = product_lookup.get(c.catalog_product_id)
        if product is None:
            continue
        if not catalog_product_is_medical(product):
            continue
        if not anchor_overlap(item_label, product.name or ""):
            continue
        kept.append(c)

    return kept
