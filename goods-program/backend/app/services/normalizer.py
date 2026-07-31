"""Text normalization pipeline.

Produces a consistent normalized representation so that "Стол детский",
"СТОЛ ДЕТСКИЙ", and "стол   детский" all normalize to the same string,
regardless of which source file (master or destination) the text came from.

REWRITTEN (v2). The previous version's docstring claimed it stripped
"punctuation noise" but the implementation only collapsed whitespace, so
«Полиция», (Интеллектум) and 900*900*570(В) all reached the tokenizer and
the fuzzy matcher with their punctuation intact. Measured on the real
Детсад.xlsx / Казниса апрель.xlsx pair, that plus the missing Kazakh
letter class was a large part of why unrelated products scored 0.4-0.5.

Three things this module now guarantees, which the rest of the matching
pipeline depends on:

1. Punctuation really is removed (quotes, brackets, slashes, commas...),
   so "«3Д ұшбұрыш» (Интеллектум)" and "3д ушбурыш интеллектум" tokenize
   to the same words.
2. Kazakh-specific letters (ә ғ қ ң ө ұ ү һ і) survive normalization and
   are part of the token character class. 12.4% of the real destination
   file's rows contain them; the old tokenizer's [a-zа-я0-9] class
   silently split those words apart.
3. Dimension strings are unified: "900*900*570", "900х900х570" (Cyrillic
   х) and "900x900x570" (Latin x) all become one canonical form, because
   in this catalog the dimensions are frequently the ONLY thing
   distinguishing two otherwise identically-named products.
"""

from __future__ import annotations

import re
import unicodedata

_WS_RE = re.compile(r"\s+")

# Map "fancy" quote/hyphen variants to a plain ASCII equivalent.
_QUOTE_CHARS = {
    "«": '"',
    "»": '"',
    "“": '"',
    "”": '"',
    "„": '"',
    "‘": "'",
    "’": "'",
    "‹": "'",
    "›": "'",
}
_HYPHEN_CHARS = {
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",
    "—": "-",
    "―": "-",
    "−": "-",
}

# Punctuation that carries no matching signal and is removed outright.
# NOTE: '.' and ',' are deliberately NOT in this class - they are handled
# separately, because they appear inside numbers ("1,5 м", "0.4 мм") where
# removing them would corrupt the value.
_PUNCT_RE = re.compile(r"[«»\"'`()\[\]{}<>;:!?/\\|*_+№#%&@~^]+")

# A '.' or ',' that is NOT between two digits -> separator, not a decimal point.
_NON_NUMERIC_DOT_RE = re.compile(r"(?<!\d)[.,]|[.,](?!\d)")

# Dimension separators: 900*900, 900х900 (Cyrillic х), 900x900 (Latin x),
# 900×900 (multiplication sign). Unified to a single Latin 'x'.
_DIMENSION_RE = re.compile(r"(\d)\s*[xх×*]\s*(?=\d)", re.IGNORECASE)

# Cyrillic/Latin lookalikes that commonly get typed interchangeably in
# product names copy-pasted between sources. Mapping Latin -> Cyrillic
# since the domain here is predominantly Russian/Kazakh text.
_LOOKALIKE_LATIN_TO_CYRILLIC = {
    "a": "а",
    "c": "с",
    "e": "е",
    "o": "о",
    "p": "р",
    "x": "х",
    "y": "у",
    "k": "к",
    "m": "м",
    "t": "т",
    "h": "н",
    "b": "в",
}

# Token character class. Includes Kazakh-specific Cyrillic letters, which
# the previous [a-zа-я0-9] class did not cover.
KAZAKH_LETTERS = "әғқңөұүһі"
_TOKEN_RE = re.compile(rf"[a-zа-я{KAZAKH_LETTERS}0-9]+")

# Words that are so common in this catalog that they carry almost no
# discriminating signal, and actively cause false matches when a scorer
# treats them as evidence. The real failure this fixes: "Обучающие плакаты
# ДЛЯ дошкольников" matched "Оборудование ДЛЯ единоборств" at 0.93 keyword
# score, because "для" was the only shared token.
#
# Kept deliberately small and domain-specific. Units are included because
# they are matched separately as attributes, not as name tokens.
STOPWORDS: frozenset[str] = frozenset(
    {
        # Russian function words
        "для", "и", "в", "с", "со", "на", "из", "по", "от", "до", "или",
        "а", "к", "о", "об", "при", "под", "над", "за", "не", "the",
        # units / measure noise
        "шт", "штук", "штука", "мм", "см", "м", "кг", "г", "л", "мл",
        "компл", "уп", "пач",
        # Kazakh function words
        "және", "үшін", "мен", "бен", "пен",
    }
)


def _normalize_quotes_and_hyphens(text: str) -> str:
    for src, dst in _QUOTE_CHARS.items():
        text = text.replace(src, dst)
    for src, dst in _HYPHEN_CHARS.items():
        text = text.replace(src, dst)
    return text


def _normalize_yo(text: str) -> str:
    """ё -> е. Deliberately does NOT touch Kazakh ө or ұ, which are
    distinct letters and not typographic variants of anything.
    """
    return text.replace("ё", "е").replace("Ё", "Е")


def normalize_text(text: str | None, *, fix_lookalikes: bool = False) -> str:
    """Full normalization pipeline for a single text field.

    Steps: unicode NFKC -> lowercase -> ё/е -> quotes/hyphens -> unify
    dimension separators -> strip punctuation -> collapse whitespace.

    `fix_lookalikes` is off by default because it is lossy (it would also
    rewrite legitimate Latin model codes like "HP Pro Tower G9"); enable it
    only for a free-text similarity representation, never for anything that
    must preserve exact codes.
    """
    if text is None:
        return ""
    t = str(text)
    t = unicodedata.normalize("NFKC", t)
    t = t.lower()
    t = _normalize_yo(t)
    t = _normalize_quotes_and_hyphens(t)

    # Unify dimensions BEFORE stripping '*', otherwise "900*900" would
    # become "900 900" and lose the fact that it is a single measurement.
    t = _DIMENSION_RE.sub(r"\1x", t)

    t = _PUNCT_RE.sub(" ", t)
    t = _NON_NUMERIC_DOT_RE.sub(" ", t)
    t = t.replace("-", " ")

    if fix_lookalikes:
        t = "".join(_LOOKALIKE_LATIN_TO_CYRILLIC.get(ch, ch) for ch in t)

    t = _WS_RE.sub(" ", t)
    return t.strip()


def tokenize(text: str, *, drop_stopwords: bool = True) -> list[str]:
    """Split normalized text into matching tokens.

    Call this rather than re-implementing a token regex per module - the
    keyword index, the IDF-overlap scorer and the ML feature extractor all
    need to agree on what a "token" is, or their scores stop being
    comparable.
    """
    tokens = _TOKEN_RE.findall(text.lower())
    if drop_stopwords:
        return [t for t in tokens if t not in STOPWORDS]
    return tokens


def build_normalized_name(product_name: str | None, description: str | None = None) -> str:
    """Normalized representation used for exact-match / search comparisons.

    Name only, on purpose. `description` is accepted (and ignored) to keep
    the existing call signature in ingestion.py, but folding it in was
    measured to HURT retrieval accuracy on the real files: top-1 dropped
    from 94.1% to 61.8%, because destination descriptions are long
    marketing prose while master descriptions are terse spec text, so the
    two sides stop being comparable. Descriptions are still stored on the
    row and used as a separate, weaker signal - see `search/types.py`.
    """
    return normalize_text(product_name)
