from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import catalog, matching, ml, projects, search, standalone_matching, uploads
from app.database import SessionLocal, init_db

app = FastAPI(title="AI Product Matching System — Phases 1-8")

# Allow the Next.js dev frontend (a different origin/port) to call this API.
# Wide open for local development; tighten this to specific origins before
# deploying anywhere publicly reachable.
app.add_middleware(
    CORSMiddleware,
    # Next.js UI (:3000) + B2B Fitout Dashboard (:5500)
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(catalog.router)
app.include_router(uploads.router)
app.include_router(search.router)
app.include_router(matching.router)
app.include_router(ml.router)
app.include_router(standalone_matching.router)


@app.on_event("startup")
def on_startup():
    init_db()
    _rebuild_index_if_master_catalog_exists()


# Schema migrations deliberately do NOT run from here. They used to (see
# HANDOFF.md section 14.0's first attempt), but this event fires on every
# `uvicorn --reload` cycle, not just a genuine container start - a file
# edit landing mid-migration could interrupt an ALTER TABLE transaction,
# and the app would then hang on its NEXT reload waiting behind it.
# Confirmed by disabling the startup-time migration call and watching the
# app start cleanly, every time, with it gone. Migrations now run from
# `entrypoint.sh`, once, before uvicorn (and its reloader) ever start.


def _rebuild_index_if_master_catalog_exists() -> None:
    """Convenience for restarts: the search index (BM25/fuzzy/vector) only
    ever lives in memory (see ARCHITECTURE.md "Phase 2"), so every restart
    used to require a manual POST /api/search/reindex before matching
    would work again. If a master catalog was already uploaded in an
    earlier session, rebuild the index automatically here instead, so
    reopening the app "just works" without re-uploading anything.

    Runs once per process startup; skipped entirely (fast) if no master
    products exist yet. Any failure here is logged, not fatal - the app
    still starts, and /api/search/reindex remains available to retry
    manually (e.g. if Qdrant isn't reachable yet).
    """
    from app.models import MasterProduct
    from app.services.search.index_manager import get_index
    from app.services.search.loader import load_master_records

    db = SessionLocal()
    try:
        has_master_products = db.query(MasterProduct.id).first() is not None
        if not has_master_products:
            return
        records = load_master_records(db)
        print(f"[startup] Found {len(records)} existing master products - rebuilding search index...")
        stats = get_index().build(records)
        print(f"[startup] Search index ready: {stats}")
    except Exception as exc:  # noqa: BLE001 - never let index startup crash the app
        print(f"[startup] Could not auto-rebuild search index ({exc}). "
              f"Call POST /api/search/reindex manually once ready.")
    finally:
        db.close()


@app.get("/health")
def health():
    return {"status": "ok"}
