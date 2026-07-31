"""Growing multi-catalog schema

Revision ID: 001_growing
Revises:
Create Date: 2026-07-17

Creates new tables and columns for versions, field defs, import mappings,
projects, and product custom fields. Prefer app.database.run_light_migrations
for additive upgrades on existing SQLite DBs; this revision documents the
target schema for fresh Alembic-managed environments.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001_growing"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tables are created via Base.metadata.create_all in app startup for MVP.
    # This revision is a no-op marker so `alembic upgrade head` succeeds.
    pass


def downgrade() -> None:
    pass
