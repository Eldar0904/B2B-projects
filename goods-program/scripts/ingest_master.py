#!/usr/bin/env python3
"""CLI: ingest a master catalog Excel file end-to-end.

Usage:
    python scripts/ingest_master.py path/to/master.xlsx [--sheet "Sheet name"]

Reads DATABASE_URL from the environment / backend/.env, same as the API.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.database import SessionLocal, init_db  # noqa: E402
from app.services.ingestion import IngestionOptions, ingest_master  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Ingest a master catalog Excel file")
    parser.add_argument("path", help="Path to the .xlsx file")
    parser.add_argument("--sheet", default=None, help="Sheet name (defaults to the largest sheet)")
    args = parser.parse_args()

    init_db()
    db = SessionLocal()
    try:
        upload = ingest_master(db, args.path, Path(args.path).name, IngestionOptions(sheet_name=args.sheet))
        db.commit()
        print(f"Upload ID: {upload.id}")
        print(f"Sheet: {upload.sheet_name}")
        print(f"Total rows: {upload.total_rows}")
        print(f"Processed: {upload.processed_rows}")
        print(f"Skipped: {upload.skipped_rows}")
        if upload.error_report:
            print(f"First errors: {upload.error_report[:5]}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
