import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# --- Force untranslated, UTF-8 diagnostics from PostgreSQL ---------------
#
# On a Russian/Kazakh Windows install, libpq emits its error messages in the
# system codepage (cp1251). psycopg2 decodes them as UTF-8, fails, and
# raises
#
#     'utf-8' codec can't decode byte 0xc2 in position 61: invalid
#     continuation byte
#
# INSTEAD of the actual error. An ordinary problem - server not running,
# database missing, wrong password - then surfaces as a codec error that
# names neither the cause nor the file, which is impossible to act on.
#
# These must be set before psycopg2 initializes libpq, hence module level.
# `setdefault` so a deliberate override in the environment still wins.
os.environ.setdefault("LC_ALL", "C")
os.environ.setdefault("LC_MESSAGES", "C")
os.environ.setdefault("PGCLIENTENCODING", "UTF8")

_is_sqlite = settings.database_url.startswith("sqlite")

if _is_sqlite:
    connect_args = {"check_same_thread": False}
else:
    # client_encoding makes the server transcode both DATA and ERROR
    # MESSAGES to UTF-8, which is what psycopg2 expects. Without it, any
    # Cyrillic in a product name or an error string is a decode hazard.
    connect_args = {"client_encoding": "utf8"}

engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables. (No Alembic migrations in this project yet; note
    that create_all creates MISSING tables but never alters an existing
    one - see scripts/migrate_add_projects.py.)
    """
    from app import models  # noqa: F401  (ensure models are registered on Base)

    Base.metadata.create_all(bind=engine)
