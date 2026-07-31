# -*- coding: utf-8 -*-
"""Migration: add the `projects` table and `uploads.project_id`.

Run once against an existing database:

    cd backend
    python -m scripts.migrate_add_projects

This project has no Alembic setup (tables are created with
`Base.metadata.create_all`, which creates missing TABLES but never alters
an existing one), so adding a column to `uploads` needs an explicit
migration. Without it, an existing product_matching.db keeps its old
`uploads` table and every query mentioning `project_id` fails with
"no such column".

The migration is idempotent and additive:

- `projects` is created only if absent.
- `uploads.project_id` is added only if absent.
- No existing row is modified. Every pre-existing upload keeps
  project_id = NULL, which is exactly what the nullable column means:
  "not part of any project". Nothing that worked before stops working.

Optionally (`--adopt-existing`) it will also create one project from the
most recent master upload and attach every existing destination upload to
it, which is usually what you want when migrating a database that already
contains an existing run.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy import inspect, text

from app.database import SessionLocal, engine


def _column_exists(connection, table: str, column: str) -> bool:
    return column in {c["name"] for c in inspect(connection).get_columns(table)}


def _table_exists(connection, table: str) -> bool:
    return table in inspect(connection).get_table_names()


def migrate(adopt_existing: bool = False) -> None:
    with engine.begin() as connection:
        if not _table_exists(connection, "uploads"):
            print("No `uploads` table found - nothing to migrate. "
                  "Start the backend once to create the schema fresh.")
            return

        if _table_exists(connection, "projects"):
            print("[skip] `projects` table already exists")
        else:
            connection.execute(
                text(
                    """
                    CREATE TABLE projects (
                        id VARCHAR(36) NOT NULL PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        description TEXT,
                        master_upload_id VARCHAR(36),
                        created_at TIMESTAMP,
                        FOREIGN KEY(master_upload_id) REFERENCES uploads (id)
                    )
                    """
                )
            )
            print("[ok]   created `projects`")

        if _column_exists(connection, "uploads", "project_id"):
            print("[skip] `uploads.project_id` already exists")
        else:
            # SQLite cannot add a column with an inline REFERENCES clause
            # to an existing table in all versions; the column alone is
            # what the ORM needs, and SQLite does not enforce FKs by
            # default anyway. On PostgreSQL the constraint is added below.
            connection.execute(text("ALTER TABLE uploads ADD COLUMN project_id VARCHAR(36)"))
            print("[ok]   added `uploads.project_id`")

            if engine.dialect.name == "postgresql":
                connection.execute(
                    text(
                        "ALTER TABLE uploads ADD CONSTRAINT uploads_project_id_fkey "
                        "FOREIGN KEY (project_id) REFERENCES projects (id)"
                    )
                )
                connection.execute(
                    text("CREATE INDEX IF NOT EXISTS ix_uploads_project_id ON uploads (project_id)")
                )
                print("[ok]   added FK constraint + index (PostgreSQL)")
            else:
                connection.execute(
                    text("CREATE INDEX IF NOT EXISTS ix_uploads_project_id ON uploads (project_id)")
                )
                print("[ok]   added index")

    if adopt_existing:
        _adopt_existing()

    print("\nMigration complete.")


def _adopt_existing() -> None:
    """Create one project from the newest master upload and attach every
    orphaned destination upload to it.

    Only touches uploads whose project_id IS NULL, so re-running is safe.
    """
    db = SessionLocal()
    try:
        master = db.execute(
            text(
                "SELECT id, filename FROM uploads WHERE upload_type = 'master' "
                "ORDER BY created_at DESC LIMIT 1"
            )
        ).first()
        if master is None:
            print("\n[skip] --adopt-existing: no master upload found")
            return

        orphan_count = db.execute(
            text("SELECT COUNT(*) FROM uploads WHERE project_id IS NULL")
        ).scalar_one()
        if not orphan_count:
            print("\n[skip] --adopt-existing: no unassigned uploads")
            return

        project_id = str(uuid.uuid4())
        db.execute(
            text(
                "INSERT INTO projects (id, name, description, master_upload_id, created_at) "
                "VALUES (:id, :name, :description, :master_upload_id, :created_at)"
            ),
            {
                "id": project_id,
                "name": f"Imported ({master.filename})",
                "description": "Created automatically by migrate_add_projects --adopt-existing.",
                "master_upload_id": master.id,
                "created_at": datetime.now(timezone.utc),
            },
        )
        db.execute(
            text("UPDATE uploads SET project_id = :pid WHERE project_id IS NULL"),
            {"pid": project_id},
        )
        db.commit()
        print(f"\n[ok]   --adopt-existing: created project {project_id!r} "
              f"from master {master.filename!r}, attached {orphan_count} upload(s)")
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--adopt-existing",
        action="store_true",
        help="Also create a project from the newest master upload and attach all "
             "currently unassigned uploads to it.",
    )
    args = parser.parse_args()
    try:
        migrate(adopt_existing=args.adopt_existing)
    except UnicodeDecodeError as exc:
        # The message from PostgreSQL could not be decoded, so the REAL
        # error is hidden behind a codec error. This is a localized-libpq
        # problem, not a migration problem - see app/database.py.
        print(
            "Migration failed: the database driver returned an error message that "
            f"could not be decoded ({exc}).\n"
            "\n"
            "This hides the real cause. Run the diagnostic, which decodes it:\n"
            "    python -m scripts.check_db\n"
            "\n"
            "Usually it means PostgreSQL is not running, the 'product_matching' "
            "database does not exist, or another PostgreSQL is occupying port 5432.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001 - surface the real reason, don't traceback-spam
        print(f"Migration failed: {exc}", file=sys.stderr)
        print("\nFor a full connection diagnosis run: python -m scripts.check_db", file=sys.stderr)
        raise SystemExit(1)
