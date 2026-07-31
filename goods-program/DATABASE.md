# Database Schema — Phase 1

Only the tables needed for the data pipeline are created in Phase 1. The
full schema from the spec (`matches`, `match_candidates`, `feedback`) is
defined in `backend/app/models.py` as a forward-compatible placeholder but
is not populated until later phases.

## `uploads`

| column         | type       | notes                                      |
|----------------|------------|---------------------------------------------|
| id             | UUID (PK)  |                                              |
| filename       | text       | original filename                           |
| upload_type    | text       | `master` \| `destination`                   |
| sheet_name     | text       | sheet ingested                              |
| status         | text       | `pending` \| `processing` \| `done` \| `failed` |
| total_rows     | int        |                                              |
| processed_rows | int        |                                              |
| skipped_rows   | int        | malformed rows that were skipped            |
| error_report   | jsonb      | list of `{row_number, reason}`               |
| created_at     | timestamptz|                                              |

## `master_products`

| column           | type        | notes                                             |
|------------------|-------------|----------------------------------------------------|
| id               | UUID (PK)   |                                                      |
| upload_id        | UUID (FK)   |                                                      |
| source_row       | int         | original row number, for traceability               |
| external_id      | text        | `Код` (e.g. `521-101-0131-0001`)                      |
| product_name     | text        | mapped from `Наименование`                           |
| normalized_name  | text        | output of normalization pipeline                     |
| description      | text        |                                                      |
| unit             | text        | `Единица измерения`                                  |
| price            | numeric     | `Сметная цена, тенге`                                |
| freight_class    | text        | `Класс груза`                                        |
| gross_weight_kg  | numeric     | `Масса брутто, кг`                                   |
| is_group_header  | boolean     | true when unit and price are both empty (section/group row, not a purchasable product) |
| raw_data         | jsonb       | full original row, untouched                         |
| created_at       | timestamptz |                                                      |

## `destination_products`

| column          | type        | notes                                       |
|-----------------|-------------|------------------------------------------------|
| id              | UUID (PK)   |                                                  |
| upload_id       | UUID (FK)   |                                                  |
| source_row      | int         | original row number, for traceability            |
| external_id     | text        | `Код` if present (often blank in this file)      |
| product_name    | text        | mapped from `Наименование товара`                |
| normalized_name | text        |                                                  |
| description     | text        |                                                  |
| quantity        | numeric     | `SUM из Кол-во`                                  |
| price           | numeric     | `Цена с НДС, в тенге`                            |
| status          | text        | `pending` \| `matched` \| `no_match`               |
| uncertainty_margin | numeric  | Phase 8: top1-top2 reranked score gap; NULL until `/prioritize` runs |
| raw_data        | jsonb       | full original row, untouched                     |
| created_at      | timestamptz |                                                  |

## Why `raw_data` on every row

Spec section 38 requires "every match must be traceable back to the
original source row" and "preserve original Excel data during processing."
Storing the full original row as JSON alongside the mapped/normalized
fields satisfies this from Phase 1 onward, regardless of how column
mapping evolves later.
