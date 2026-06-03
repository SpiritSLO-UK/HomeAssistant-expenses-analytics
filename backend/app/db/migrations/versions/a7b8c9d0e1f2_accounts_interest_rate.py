"""accounts.interest_rate (per-account savings interest rate)

Adds an optional annual interest rate (percent) to accounts so savings pots can
show projected growth. Nullable — no behaviour change for existing rows.

Revision ID: a7b8c9d0e1f2
Revises: d4e5f6a7b8c9
Create Date: 2026-06-04 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7b8c9d0e1f2'
down_revision: str | None = 'd4e5f6a7b8c9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('accounts', sa.Column('interest_rate', sa.Numeric(6, 3), nullable=True))


def downgrade() -> None:
    op.drop_column('accounts', 'interest_rate')
