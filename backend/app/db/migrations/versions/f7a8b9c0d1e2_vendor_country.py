"""vendors.country (spend-by-location map)

Adds an optional ISO-3166 alpha-2 country code to vendors, used by the
spend-by-country breakdown (a transaction's country = its vendor's country if
set, else inferred from the currency).

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-06-03 15:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f7a8b9c0d1e2'
down_revision: str | None = 'e6f7a8b9c0d1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('vendors', sa.Column('country', sa.String(length=2), nullable=True))


def downgrade() -> None:
    op.drop_column('vendors', 'country')
