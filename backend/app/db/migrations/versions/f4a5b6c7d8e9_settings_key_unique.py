"""settings.key unique — one row per key

The settings service reads and upserts by ``key`` alone, so a duplicate key would
silently shadow another row (whichever ``.first()`` happened to return). This
collapses any existing duplicates (keeping the most recent row per key) and makes
``ix_settings_key`` a UNIQUE index so duplicates can't recur (SR-2).

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-06-27 00:00:00.000000
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f4a5b6c7d8e9'
down_revision: str | None = 'e3f4a5b6c7d8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Collapse duplicate keys first (keep the highest id = most recently inserted),
    # else creating the unique index would fail. Portable across SQLite + Postgres.
    op.execute(
        "DELETE FROM settings WHERE id NOT IN (SELECT MAX(id) FROM settings GROUP BY key)"
    )
    op.drop_index('ix_settings_key', table_name='settings')
    op.create_index('ix_settings_key', 'settings', ['key'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_settings_key', table_name='settings')
    op.create_index('ix_settings_key', 'settings', ['key'], unique=False)
