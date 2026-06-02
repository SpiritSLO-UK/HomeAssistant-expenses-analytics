"""transactions.is_business + vat_amount (business / VAT receipts)

Adds a business-vs-personal flag and an optional VAT amount (in the transaction's
own currency) so expenses can be flagged for claiming and VAT totalled. Both
default to off/NULL for every existing row — no aggregate reads them, so no
behaviour change until a transaction is marked business.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-06-02 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: str | None = 'c3d4e5f6a7b8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'transactions',
        sa.Column('is_business', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column('transactions', sa.Column('vat_amount', sa.Numeric(14, 2), nullable=True))


def downgrade() -> None:
    op.drop_column('transactions', 'vat_amount')
    op.drop_column('transactions', 'is_business')
