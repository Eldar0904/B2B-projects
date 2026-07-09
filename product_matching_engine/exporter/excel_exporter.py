"""
excel_exporter.py

Writes the final Top-3-per-item match results to Match_Results.xlsx.

Kept deliberately dumb: it takes a flat list of result rows (already
computed by main.py) and writes them out with light formatting. No
matching logic lives here so the export format can evolve independently.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

RESULT_COLUMNS = [
    "Item Code",
    "Item Name",
    "Rank",
    "Government Code",
    "Product Name",
    "Brand",
    "Similarity Score",
    "Confidence (%)",
    "Explanation",
]


def export_results(rows: list[dict], output_path: str | Path) -> None:
    """rows: list of dicts with keys matching RESULT_COLUMNS (order-insensitive)."""
    df = pd.DataFrame(rows, columns=RESULT_COLUMNS)

    output_path = Path(output_path)
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Match Results")
        sheet = writer.sheets["Match Results"]

        # Light column-width auto-fit for readability.
        for col_idx, col_name in enumerate(RESULT_COLUMNS, start=1):
            max_len = max(
                [len(str(col_name))]
                + [len(str(v)) for v in df[col_name].astype(str).tolist()]
            )
            sheet.column_dimensions[sheet.cell(row=1, column=col_idx).column_letter].width = min(
                max(12, max_len + 2), 60
            )
