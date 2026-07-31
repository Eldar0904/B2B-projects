"""Ingestion orchestration: Excel -> column mapping -> normalization -> DB.

Spec section 35 (error handling): the application must never crash because
one row is malformed. Every row is processed independently; failures are
logged into the upload's error_report and the row is skipped, while the
rest of the batch continues.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.models import DestinationProduct, MasterProduct, Upload
from app.services import excel_reader
from app.services.attributes import extract_attributes
from app.services.column_mapper import apply_manual_overrides, auto_map_columns
from app.services.normalizer import build_normalized_name

_NUMERIC_JUNK_RE = re.compile(r"[^\d,.\-]")

# Bounded VARCHAR column lengths from app/models.py. Values are truncated
# to fit rather than left unbounded, because real spreadsheets sometimes
# put much longer text into a "code" column than expected (e.g. this
# project's real master catalog has section-header rows where the entire
# multi-line category title sits in the "Код" column instead of a real
# product code). SQLite never enforces VARCHAR length limits, so this only
# surfaces as a hard crash once running against real PostgreSQL - caught
# via real-world use, not caught by earlier SQLite-only testing. The full,
# untruncated original value is always still preserved in raw_data, so
# nothing is actually lost by truncating the mapped column.
_EXTERNAL_ID_MAX = 255
_UNIT_MAX = 64
_FREIGHT_CLASS_MAX = 64


def _to_number(value) -> float | None:
    """Best-effort numeric parse. Returns None (never raises) on failure."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = _NUMERIC_JUNK_RE.sub("", text)
    if not text:
        return None
    # Handle "1 234,56" / "1234.56" / "1234,56" style numbers.
    if "," in text and "." in text:
        text = text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _to_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_str_limited(value, max_length: int) -> str | None:
    """Like `_to_str`, but truncates to fit a bounded VARCHAR column
    instead of letting an oversized value reach the database at all.
    """
    text = _to_str(value)
    if text is None:
        return None
    return text[:max_length]


@dataclass
class IngestionOptions:
    sheet_name: str | None = None
    manual_column_mapping: dict[str, str] | None = None
    commit_batch_size: int = 500


def _resolve_sheet(path: str, options: IngestionOptions) -> excel_reader.SheetSummary:
    sheets = excel_reader.list_sheets(path)
    if not sheets:
        raise ValueError("Workbook contains no sheets")
    if options.sheet_name:
        for s in sheets:
            if s.name == options.sheet_name:
                return s
        raise ValueError(f"Sheet '{options.sheet_name}' not found. Available: {[s.name for s in sheets]}")
    # Default: the sheet with the most rows (the actual data sheet, as
    # opposed to a small helper/lookup sheet).
    return max(sheets, key=lambda s: s.row_count)


def _flush_batch_with_row_fallback(
    db: Session, batch: list, errors: list[dict], source_rows: list[int]
) -> tuple[int, int]:
    """Flush a batch of pending records. If the batch as a whole fails at
    the database level (e.g. a constraint violation SQLite never caught
    but PostgreSQL correctly enforces), fall back to committing the batch
    one row at a time so a single bad row can never take the rest of a
    500-row batch down with it - the same "never crash on one malformed
    row" guarantee the per-row try/except above already gives for
    Python-level errors, extended to cover database-level errors too.
    """
    try:
        db.flush()
        return len(batch), 0
    except (DataError, IntegrityError) as exc:
        db.rollback()
        succeeded = 0
        failed = 0
        for record, source_row in zip(batch, source_rows):
            try:
                db.add(record)
                db.flush()
                succeeded += 1
            except (DataError, IntegrityError) as row_exc:
                db.rollback()
                failed += 1
                errors.append({"row": source_row, "reason": str(row_exc.orig if hasattr(row_exc, "orig") else row_exc)})
        return succeeded, failed


def _ingest(
    db: Session,
    path: str,
    filename: str,
    upload_type: str,
    options: IngestionOptions,
) -> Upload:
    sheet = _resolve_sheet(path, options)
    auto_mapping, unmapped = auto_map_columns(sheet.headers)
    mapping = apply_manual_overrides(auto_mapping, options.manual_column_mapping)

    upload = Upload(
        filename=filename,
        upload_type=upload_type,
        sheet_name=sheet.name,
        status="processing",
        total_rows=max(sheet.row_count - sheet.header_row_index - 1, 0),
    )
    db.add(upload)
    db.flush()

    errors: list[dict] = []
    processed = 0
    skipped = 0
    pending_records: list = []
    pending_source_rows: list[int] = []

    for source_row, row in excel_reader.iter_rows(path, sheet.name, sheet.header_row_index):
        try:
            record = _build_record(upload_type, upload.id, source_row, row, mapping)
        except Exception as exc:  # noqa: BLE001 - intentionally broad, must never crash the batch
            skipped += 1
            errors.append({"row": source_row, "reason": str(exc)})
            continue

        db.add(record)
        pending_records.append(record)
        pending_source_rows.append(source_row)

        if len(pending_records) >= options.commit_batch_size:
            succeeded, failed = _flush_batch_with_row_fallback(db, pending_records, errors, pending_source_rows)
            processed += succeeded
            skipped += failed
            pending_records = []
            pending_source_rows = []

    if pending_records:
        succeeded, failed = _flush_batch_with_row_fallback(db, pending_records, errors, pending_source_rows)
        processed += succeeded
        skipped += failed

    upload.processed_rows = processed
    upload.skipped_rows = skipped
    upload.error_report = errors[:1000] or None  # cap stored errors to keep row reasonably sized
    upload.status = "done"
    db.flush()
    return upload


def _build_record(upload_type: str, upload_id: str, source_row: int, row: dict, mapping: dict[str, str]):
    def field(canonical_name: str):
        header = mapping.get(canonical_name)
        return row.get(header) if header else None

    product_name = _to_str(field("product_name"))
    description = _to_str(field("description"))
    normalized_name = build_normalized_name(product_name, description)

    # HANDOFF.md section 5 (Task 2): parsed dimensions/material/unit,
    # additive only - product_name/description above are never touched by
    # this. `field("dimensions")` is the raw "Размеры"/"Габариты" column
    # when the sheet has one (column_mapper.py); attributes.py prefers it
    # over parsing the name/description when both are present. Shared
    # between master and destination, since both sides need the same
    # fields populated the same way for a later attribute-comparison signal
    # to be meaningful.
    attrs = extract_attributes(
        product_name,
        description,
        raw_dimensions=_to_str(field("dimensions")),
        raw_unit=_to_str(field("unit")),
    )

    if upload_type == "master":
        unit = _to_str_limited(field("unit"), _UNIT_MAX)
        price = _to_number(field("price"))
        is_group_header = unit is None and price is None
        return MasterProduct(
            upload_id=upload_id,
            source_row=source_row,
            external_id=_to_str_limited(field("external_id"), _EXTERNAL_ID_MAX),
            product_name=product_name,
            normalized_name=normalized_name,
            description=description,
            unit=unit,
            price=price,
            freight_class=_to_str_limited(field("freight_class"), _FREIGHT_CLASS_MAX),
            gross_weight_kg=_to_number(field("gross_weight_kg")),
            is_group_header=is_group_header,
            dim_w_mm=attrs.dim_w_mm,
            dim_h_mm=attrs.dim_h_mm,
            dim_d_mm=attrs.dim_d_mm,
            material=attrs.material,
            unit_normalized=attrs.unit_normalized,
            raw_data=_json_safe(row),
        )

    if upload_type == "destination":
        quantity = _to_number(field("quantity"))
        return DestinationProduct(
            upload_id=upload_id,
            source_row=source_row,
            external_id=_to_str_limited(field("external_id"), _EXTERNAL_ID_MAX),
            product_name=product_name,
            normalized_name=normalized_name,
            description=description,
            quantity=quantity,
            price=_to_number(field("price")),
            status="pending",
            dim_w_mm=attrs.dim_w_mm,
            dim_h_mm=attrs.dim_h_mm,
            dim_d_mm=attrs.dim_d_mm,
            material=attrs.material,
            unit_normalized=attrs.unit_normalized,
            # Mirrors `quantity` for now - see models.py's DestinationProduct
            # docstring comment. Reserved as its own column so a future
            # per-unit conversion (once real conversion factors exist, e.g.
            # "1 компл = 4 шт") doesn't require another migration on top of
            # this one; it is NOT a different value today.
            quantity_normalized=quantity,
            raw_data=_json_safe(row),
        )

    raise ValueError(f"Unknown upload_type: {upload_type}")


def _json_safe(row: dict) -> dict:
    """Coerce a raw openpyxl row (which may contain datetimes etc.) into
    JSON-serializable values, preserving it for traceability without losing
    the original content.
    """
    safe = {}
    for k, v in row.items():
        if v is None or isinstance(v, (str, int, float, bool)):
            safe[k] = v
        else:
            safe[k] = str(v)
    return safe


def ingest_master(db: Session, path: str, filename: str, options: IngestionOptions | None = None) -> Upload:
    return _ingest(db, path, filename, "master", options or IngestionOptions())


def ingest_destination(db: Session, path: str, filename: str, options: IngestionOptions | None = None) -> Upload:
    return _ingest(db, path, filename, "destination", options or IngestionOptions())
