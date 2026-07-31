#!/bin/sh
# Runs once per real container start, BEFORE uvicorn (and its --reload
# watcher) begin - this is the fix for a real bug (HANDOFF.md section
# 14.0): migrations were originally run from inside app/main.py's
# @app.on_event("startup") handler, which fires on every uvicorn --reload
# cycle, not just a genuine container start. A file edit landing mid-
# migration could interrupt an ALTER TABLE transaction, and the next
# reload's migration attempt would then hang behind it - confirmed by
# disabling the startup-time migration call and watching the app start
# cleanly every time. Running migrations here instead means they happen
# exactly once per `docker compose up`/container start, never again on
# every hot-reload during a dev session.
set -e

echo "[entrypoint] Running database migrations..."
python -m scripts.migrate_add_projects || echo "[entrypoint] migrate_add_projects failed - continuing anyway (see HANDOFF.md section 2 for known Postgres/encoding issues, or run it manually to see the full error)"
python -m scripts.migrate_add_catalog_versions || echo "[entrypoint] migrate_add_catalog_versions failed - continuing anyway"
python -m scripts.migrate_add_attributes || echo "[entrypoint] migrate_add_attributes failed - continuing anyway"
python -m scripts.migrate_add_master_product_active_flag || echo "[entrypoint] migrate_add_master_product_active_flag failed - continuing anyway"
python -m scripts.migrate_add_master_product_updated_at || echo "[entrypoint] migrate_add_master_product_updated_at failed - continuing anyway"

echo "[entrypoint] Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
