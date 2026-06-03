"""transactions.country (per-transaction country for the spend-by-location map)

A precise, optional country per transaction (e.g. set for a trip to Spain → ES).
Highest-precedence signal for the spend-by-country breakdown, above the vendor
country and the currency fallback.

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-06-03 17:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b9c0d1e2f3a4'
down_revision: str | None = 'a8b9c0d1e2f3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('transactions', sa.Column('country', sa.String(length=2), nullable=True))


def downgrade() -> None:
    op.drop_column('transactions', 'country')
