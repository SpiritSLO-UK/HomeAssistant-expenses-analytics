"""import_profiles.date_format (per-profile CSV date order, BUG-CSV-USDATE)

Pins how a saved import profile reads ambiguous CSV dates: "auto" (heuristic
per-file detection, the historic behaviour), "dmy" (force UK day-first DD/MM) or
"mdy" (force US month-first MM/DD). server_default 'auto' backfills existing rows
so behaviour is unchanged. Maps to GenericCsvParser(month_first=…).

Revision ID: c6d7e8f9a0b1
Revises: c5d6e7f8a9b0
Create Date: 2026-07-17 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c6d7e8f9a0b1'
down_revision: str | None = 'c5d6e7f8a9b0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'import_profiles',
        sa.Column('date_format', sa.String(length=8), nullable=False, server_default='auto'),
    )


def downgrade() -> None:
    op.drop_column('import_profiles', 'date_format')
