"""
excel_reader.py

Generic Excel ingestion layer.

Design goal: the rest of the matching engine (normalizer, embeddings, matcher)
must never know whether a "catalog" came from a government spreadsheet, a
supplier price list, or any other source. This module's job is to read an
Excel file and hand back a pandas DataFrame with a predictable, minimal set
of columns.

To support swapping the Government Catalog for a Supplier Catalog later
without touching matching logic, catalogs are read through `read_catalog()`
with a `column_map` that translates each source file's own column names into
the engine's canonical schema:

    canonical_code, name, brand, model, description, specs, price

Only `canonical_code`, `name` and `description` are required; anything else
missing is filled with an empty string so downstream code can rely on the
columns always existing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

# Canonical schema used everywhere past this module.
CANONICAL_ITEM_COLUMNS = ["item_code", "item_name", "description", "quantity"]
CANONICAL_CATALOG_COLUMNS = [
    "canonical_code",
    "name",
    "brand",
    "model",
    "description",
    "specs",
    "price",
]


@dataclass
class CatalogColumnMap:
    """Maps a source spreadsheet's column names to the canonical schema.

    Only `code_col`, `name_col` and `description_col` are mandatory. Leave
    any optional field as None if the source file doesn't have it.
    """

    code_col: str
    name_col: str
    description_col: str
    brand_col: Optional[str] = None
    model_col: Optional[str] = None
    specs_col: Optional[str] = None
    price_col: Optional[str] = None


# Default mapping for the Government Catalog described in the spec.
GOVERNMENT_CATALOG_MAP = CatalogColumnMap(
    code_col="Government Code",
    name_col="Product Name",
    brand_col="Brand",
    model_col="Model",
    description_col="Description",
    specs_col="Technical Specifications",
    price_col="Price",
)

# Default mapping for Our Items.
OUR_ITEMS_MAP = {
    "item_code": "Item Code",
    "item_name": "Item Name",
    "description": "Description",
    "quantity": "Quantity",
}


def read_our_items(path: str | Path, column_map: dict | None = None) -> pd.DataFrame:
    """Read the internal item list into the canonical schema.

    Parameters
    ----------
    path: path to Our_Items.xlsx
    column_map: optional override of {canonical_name: source_column_name}
    """
    column_map = column_map or OUR_ITEMS_MAP
    df = pd.read_excel(path)

    missing = [src for src in column_map.values() if src not in df.columns]
    if missing:
        raise ValueError(
            f"Our items file '{path}' is missing expected column(s): {missing}. "
            f"Found columns: {list(df.columns)}"
        )

    out = pd.DataFrame()
    for canonical, source in column_map.items():
        out[canonical] = df[source]

    for col in CANONICAL_ITEM_COLUMNS:
        if col not in out.columns:
            out[col] = "" if col != "quantity" else 0

    out["item_code"] = out["item_code"].astype(str).str.strip()
    out["item_name"] = out["item_name"].fillna("").astype(str)
    out["description"] = out["description"].fillna("").astype(str)

    return out[CANONICAL_ITEM_COLUMNS]


def read_catalog(path: str | Path, column_map: CatalogColumnMap) -> pd.DataFrame:
    """Read any product catalog (government, supplier, etc.) into the
    canonical catalog schema, using `column_map` to translate column names.

    This is the extension point: to support a new supplier catalog with
    different headers, build a new `CatalogColumnMap` and call this same
    function. No other module needs to change.
    """
    df = pd.read_excel(path)

    required = {
        "canonical_code": column_map.code_col,
        "name": column_map.name_col,
        "description": column_map.description_col,
    }
    missing = [src for src in required.values() if src not in df.columns]
    if missing:
        raise ValueError(
            f"Catalog file '{path}' is missing required column(s): {missing}. "
            f"Found columns: {list(df.columns)}"
        )

    out = pd.DataFrame()
    out["canonical_code"] = df[column_map.code_col].astype(str).str.strip()
    out["name"] = df[column_map.name_col].fillna("").astype(str)
    out["description"] = df[column_map.description_col].fillna("").astype(str)

    optional_cols = {
        "brand": column_map.brand_col,
        "model": column_map.model_col,
        "specs": column_map.specs_col,
        "price": column_map.price_col,
    }
    for canonical, source in optional_cols.items():
        if source and source in df.columns:
            out[canonical] = df[source].fillna("").astype(str)
        else:
            out[canonical] = ""

    return out[CANONICAL_CATALOG_COLUMNS]


def read_government_catalog(path: str | Path) -> pd.DataFrame:
    """Convenience wrapper around `read_catalog` for the Government Catalog
    format described in the project spec.
    """
    return read_catalog(path, GOVERNMENT_CATALOG_MAP)
