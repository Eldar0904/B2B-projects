"""Catalog matcher pipeline.

Reads:
- NEEDS: C:/Users/Pine/Documents/Claude/Projects/B2B projects/Книга1.xlsx
- CATALOG: C:/Users/Pine/Documents/Claude/Projects/B2B projects/Казниса апрель.xlsx
Writes: C:/Users/Pine/Documents/Claude/Projects/B2B projects/catalog_matches.xlsx
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

NEEDS_PATH = Path("C:/Users/Pine/Documents/Claude/Projects/B2B projects/Книга1.xlsx")
CATALOG_PATH = Path("C:/Users/Pine/Documents/Claude/Projects/B2B projects/Казниса апрель.xlsx")
OUT_PATH = Path("C:/Users/Pine/Documents/Claude/Projects/B2B projects/catalog_matches.xlsx")

MATCH_TYPE_HIGH = "exact-like"
MATCH_TYPE_MED = "related"
MATCH_TYPE_MISS = "missing"
MIN_SCORE = 0.05  # reject too-low matches
TOP_K = 3
MAX_CANDIDATES = 20000


def load_needs(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=0, engine="openpyxl")
    # Normalize headers
    df.columns = [str(c).strip() for c in df.columns]
    print("NEEDS columns:", df.columns.tolist(), file=sys.stderr)
    needed = {"Наименование", "Код", "Категория", "Описание", "Ед.изм.", "Кол-во"}
    missing_cols = [c for c in needed if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Needs file missing columns: {missing_cols}")
    df = df.dropna(subset=["Наименование"]).copy()
    df = df[df["Наименование"].astype(str).str.strip() != ""].copy()
    df["__index"] = range(1, len(df) + 1)
    df["need_text"] = (
        df["Наименование"].astype(str)
        + " "
        + df["Описание"].fillna("").astype(str)
    )
    return df.reset_index(drop=True)


def load_catalog(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path, sheet_name=0, engine="openpyxl")
    df.columns = [str(c).strip() for c in df.columns]
    print("CATALOG columns:", df.columns.tolist(), file=sys.stderr)
    required = {"Код", "Наименование", "Описание", "Единица измерения", "Сметная цена, тенге", "поисковый текст"}
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Catalog file missing columns: {missing}")
    df = df.dropna(subset=["Наименование"]).copy()
    df["catalog_text"] = (
        df["Наименование"].astype(str)
        + " "
        + df["Описание"].fillna("").astype(str)
        + " "
        # include search text column 8?
        df["поисковый текст"].fillna("").astype(str)
    )
    price_col = "Сметная цена, тенге"
    df[price_col] = pd.to_numeric(df[price_col], errors="coerce")
    return df.reset_index(drop=True)


def tokenize(text: str) -> set[str]:
    text = text.lower()
    text = re.sub(r"[^\p{L}\p{N}\s]+", " ", text, flags=re.UNICODE)
    toks = [t for t in text.split() if len(t) > 1]
    return set(toks)


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def token_overlap_fraction(a: set[str], b: set[str]) -> float:
    if not a:
        return 0.0
    inter = len(a & a)
    return inter / len(a)


def score_token_overlap(need_text: str, catalog_text: str) -> float:
    nt = tokenize(need_text)
    ct = tokenize(catalog_text)
    return jaccard(nt, ct)


def build_similarity_matrix(need_texts, catalog_texts):
    # TF-IDF cosine similarity as main signal; for fewer than 3000 docs use Python list to avoid CPU issues
    docs = list(need_texts) + list(catalog_texts)
    vect = TfidfVectorizer(
        analyzer="word",
        lowercase=True,
        ngram_range=(1, 2),
        max_features=20000,
        token_pattern=r"(?u)\b\w+\b",
    )
    mat = vect.fit_transform(docs)
    need_mat = mat[: len(need_texts)]
    cat_mat = mat[len(need_texts) :]
    sim = cosine_similarity(need_mat, cat_mat)
    return sim


def prepare_candidates(scores, top_k: int = TOP_K, min_score: float = MIN_SCORE):
    # scores: 2D numpy array (n_need, n_catalog)
    out = []
    for i, row in enumerate(scores):
        idx_sorted = sorted(range(len(row)), key=row.__getitem__, reverse=True)[:top_k]
        items = [(j, float(row[j])) for j in idx_sorted]
        # filter min score
        filtered = [(j, s) for j, s in items if s >= min_score]
        out.append(filtered)
    return out


def main() -> int:
    print("Loading needs...")
    needs = load_needs(NEEDS_PATH)
    print(f"Needs rows: {len(needs)}")
    print("Loading catalog...")
    catalog = load_catalog(CATALOG_PATH)
    print(f"Catalog rows: {len(catalog)}")

    # Main candidate retrieval with TF-IDF cosine + token fallback
    sim_scores = build_similarity_matrix(needs["need_text"].tolist(), catalog["catalog_text"].tolist())
    cand_lists = prepare_candidates(sim_scores, TOP_K, MIN_SCORE)

    matched = 0
    missing = 0
    rows = []
    for i, need in needs.iterrows():
        matches = cand_lists[i]
        if not matches:
            missing += 1
            rows.append(
                {
                    "need_index": int(need["__index"]),
                    "need_name": need["Наименование"],
                    "need_code": need.get("Код"),
                    "need_category": need.get("Категория"),
                    "need_qty": need.get("Кол-во"),
                    "match_rank": None,
                    "catalog_code": None,
                    "catalog_name": None,
                    "catalog_category": None,
                    "catalog_price_tg": None,
                    "score": None,
                    "match_type": MATCH_TYPE_MISS,
                }
            )
            continue
        matched += 1
        for rank, (j, score) in enumerate(matches, start=1):
            cat = catalog.iloc[j]
            # refine score with token overlap
            s = float(score)
            tok = score_token_overlap(str(need["need_text"]), str(cat["catalog_text"]))
            final = 0.75 * s + 0.25 * tok
            mtype = MATCH_TYPE_HIGH if final >= 0.2 else MATCH_TYPE_MED
            rows.append(
                {
                    "need_index": int(need["__index"]),
                    "need_name": need["Наименование"],
                    "need_code": need.get("Код"),
                    "need_category": need.get("Категория"),
                    "need_qty": need.get("Кол-во"),
                    "match_rank": rank,
                    "catalog_code": cat.get("Код"),
                    "catalog_name": cat.get("Наименование"),
                    "catalog_category": cat.get("Единица измерения"),
                    "catalog_price_tg": cat.get("Сметная цена, тенге"),
                    "score": round(final, 4),
                    "match_type": mtype,
                }
            )

    matches_df = pd.DataFrame(rows)
    top_scores = matches_df.loc[matches_df["match_type"] != MATCH_TYPE_MISS, "score"].tolist()
    hist = pd.cut(top_scores, bins=[0, 0.1, 0.2, 0.5, 1.0], include_lowest=True).value_counts().sort_index()
    summary = pd.DataFrame(
        {
            "metric": ["total_needs", "matched", "missing", "matches_with_high_score", "matches_with_med_score"],
            "value": [
                int(len(needs)),
                int(matched),
                int(missing),
                int((matches_df["match_type"] == MATCH_TYPE_HIGH).sum()),
                int((matches_df["match_type"] == MATCH_TYPE_MED).sum()),
            ],
        }
    )
    score_dist = hist.reset_index()
    score_dist.columns = ["score_bin", "count"]
    with pd.ExcelWriter(OUT_PATH, engine="openpyxl") as writer:
        matches_df.to_excel(writer, sheet_name="matches", index=False)
        summary.to_excel(writer, sheet_name="summary", index=False)
        score_dist.to_excel(writer, sheet_name="score_distribution", index=False)
    print(f"Wrote: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
