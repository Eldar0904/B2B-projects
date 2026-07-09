"""
main.py

Orchestrates the Product Matching Engine end to end:

    Our_Items.xlsx ---\
                        > normalize -> embed -> semantic top-20 -> re-rank -> Match_Results.xlsx
    Government_Catalog.xlsx --/

Run:
    python main.py \
        --items Our_Items.xlsx \
        --catalog Government_Catalog.xlsx \
        --output Match_Results.xlsx

Swapping the catalog source: pass a different --catalog file plus a custom
CatalogColumnMap in code (see importer.excel_reader) — nothing else in this
pipeline needs to change.
"""

from __future__ import annotations

import argparse
import sys

from importer.excel_reader import read_our_items, read_government_catalog
from normalizer.text_normalizer import normalize_text, build_search_text
from embeddings.embedding_service import EmbeddingService
from matcher.semantic_matcher import SemanticMatcher
from matcher.llm_ranker import HeuristicRanker
from exporter.excel_exporter import export_results

TOP_K_CANDIDATES = 20
TOP_N_RESULTS = 3


def build_catalog_search_texts(catalog_df) -> list[str]:
    texts = []
    for _, row in catalog_df.iterrows():
        texts.append(
            build_search_text(
                normalize_text(row["name"]),
                normalize_text(row["brand"]),
                normalize_text(row["model"]),
                normalize_text(row["description"]),
                normalize_text(row["specs"]),
            )
        )
    return texts


def build_item_search_text(item_row) -> str:
    return build_search_text(
        normalize_text(item_row["item_name"]),
        normalize_text(item_row["description"]),
    )


def run(items_path: str, catalog_path: str, output_path: str, top_k: int = TOP_K_CANDIDATES,
        top_n: int = TOP_N_RESULTS) -> None:
    print(f"Reading items from {items_path} ...")
    items_df = read_our_items(items_path)
    print(f"Reading catalog from {catalog_path} ...")
    catalog_df = read_government_catalog(catalog_path)

    print(f"Normalizing and building search text for {len(catalog_df)} catalog rows ...")
    catalog_texts = build_catalog_search_texts(catalog_df)

    print("Fitting embedding model on catalog corpus ...")
    matcher = SemanticMatcher(embedding_service=EmbeddingService())
    matcher.index_catalog(catalog_df["canonical_code"].tolist(), catalog_texts)

    ranker = HeuristicRanker()

    result_rows: list[dict] = []
    print(f"Matching {len(items_df)} items against catalog ...")
    for _, item in items_df.iterrows():
        query_text = build_item_search_text(item)
        candidates = matcher.top_k(query_text, k=top_k)

        candidate_dicts = []
        for cand in candidates:
            row = catalog_df.iloc[cand.index]
            candidate_dicts.append(
                {
                    "canonical_code": row["canonical_code"],
                    "name": row["name"],
                    "brand": row["brand"],
                    "model": row["model"],
                    "description": row["description"],
                    "specs": row["specs"],
                    "similarity": cand.similarity,
                }
            )

        ranked = ranker.rank(
            query={"item_name": item["item_name"], "description": item["description"]},
            candidates=candidate_dicts,
        )

        for rank, match in enumerate(ranked[:top_n], start=1):
            result_rows.append(
                {
                    "Item Code": item["item_code"],
                    "Item Name": item["item_name"],
                    "Rank": rank,
                    "Government Code": match.catalog_code,
                    "Product Name": match.product_name,
                    "Brand": match.brand,
                    "Similarity Score": match.similarity_score,
                    "Confidence (%)": match.confidence,
                    "Explanation": match.explanation,
                }
            )

    print(f"Writing results to {output_path} ...")
    export_results(result_rows, output_path)
    print("Done.")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Product Matching Engine")
    parser.add_argument("--items", default="Our_Items.xlsx", help="Path to Our_Items.xlsx")
    parser.add_argument("--catalog", default="Government_Catalog.xlsx", help="Path to Government_Catalog.xlsx")
    parser.add_argument("--output", default="Match_Results.xlsx", help="Path to write results")
    parser.add_argument("--top-k", type=int, default=TOP_K_CANDIDATES, help="Semantic candidates per item")
    parser.add_argument("--top-n", type=int, default=TOP_N_RESULTS, help="Final matches returned per item")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = parse_args()
    run(args.items, args.catalog, args.output, top_k=args.top_k, top_n=args.top_n)
