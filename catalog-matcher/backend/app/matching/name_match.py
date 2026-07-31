"""
Name-level matching helpers.

Retriever scores (especially embeddings) can be high when only a material
adjective or dimension overlaps («резиновый», «170 см»). These functions
re-check the actual product names before a candidate is shown.
"""
from __future__ import annotations

import re
from typing import List, Set

from rapidfuzz import fuzz

from app.matching.text_similarity import fuzzy_name_score

# Tokens that must not be the sole reason two products match.
_WEAK_TOKENS: Set[str] = {
    "резиновый", "резиновая", "резиновое", "резиновые",
    "одноразовые", "одноразовый", "одноразовая",
    "напольный", "напольная", "настольный", "настольная",
    "пластиковый", "пластиковая", "металлический", "металлическая",
    "деревянный", "деревянная", "электрический", "электрическая",
    "медицинский", "медицинская", "медицинские",
    "профессиональный", "профессиональная",
    "многофункциональный", "многофункциональная",
    "размер", "размеры", "мм", "см", "метр", "метра",
}

_STOPWORDS: Set[str] = {
    "для", "и", "в", "на", "по", "из", "с", "со", "к", "от", "до", "при",
    "без", "или", "не", "шт", "комплект", "набор", "тип", "модель",
    "the", "and", "for", "with",
}

_TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9]+")


def _tokenize(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def significant_tokens(text: str, limit: int = 6) -> List[str]:
    """Meaningful words from a product name (skip fillers and weak adjectives)."""
    out: List[str] = []
    for tok in _tokenize(text):
        if tok in _STOPWORDS or tok in _WEAK_TOKENS:
            continue
        if tok.isdigit():
            continue
        if len(tok) < 3:
            continue
        out.append(tok)
        if len(out) >= limit:
            break
    return out


def _tokens_related(a: str, b: str) -> bool:
    if a == b:
        return True
    if a in b or b in a:
        return True
    if fuzz.ratio(a, b) >= 86:
        return True
    # шприцы ↔ шприц, гитара ↔ гитары
    n = min(len(a), len(b), 5)
    if n >= 4 and a[:n] == b[:n]:
        return True
    return False


def anchor_overlap(item_text: str, product_text: str) -> bool:
    """
    True when at least one significant item token relates to a product token.

    Prevents «грелка резиновая» matching «мяч резиновый» on material alone.
    """
    item_anchors = significant_tokens(item_text)
    if not item_anchors:
        return True

    product_tokens = significant_tokens(product_text, limit=12)
    if not product_tokens:
        return False

    for anchor in item_anchors:
        for pt in product_tokens:
            if _tokens_related(anchor, pt):
                return True
    return False


def combined_name_score(item_text: str, product_text: str) -> float:
    """Direct fuzzy score between item and catalog product names."""
    return fuzzy_name_score(item_text.strip(), product_text.strip())


def adjust_candidate_score(
    item_text: str,
    product_text: str,
    retriever_score: float,
    *,
    code_matched: bool = False,
) -> tuple[float, str]:
    """
    Blend retriever score with name-level checks.

    Returns (adjusted_score, note_for_explanation).
    """
    if code_matched:
        return retriever_score, ""

    name_score = combined_name_score(item_text, product_text)
    has_anchors = anchor_overlap(item_text, product_text)

    # Conservative: never show higher confidence than name similarity supports.
    adjusted = min(retriever_score, name_score)

    if not has_anchors:
        adjusted *= 0.35
        note = "no keyword overlap"
    elif name_score < 0.45:
        adjusted *= 0.6
        note = f"weak name match ({name_score:.2f})"
    else:
        note = f"name match ({name_score:.2f})"

    return round(min(1.0, adjusted), 4), note
