"""receipts.ai_category_id (reuse the receipt's AI-suggested category on its txn)

When a receipt image is sent to AI, that one call now also returns a suggested
category. We persist it here so the matched/created transaction can reuse it —
avoiding a second AI call just to categorise the transaction (backlog #110).

Revision ID: e7f8a9b0c1d2
Revises: c0d1e2f3a4b5
Create Date: 2026-06-06 10:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7f8a9b0c1d2'
down_revision: str | None = 'c0d1e2f3a4b5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain column (no inline FK): SQLite can't ALTER-add a constraint. The model
    # declares the FK for fresh create_all DBs; on upgraded SQLite the reuse code
    # guards against a dangling id by checking the category still exists.
    op.add_column('receipts', sa.Column('ai_category_id', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('receipts', 'ai_category_id')
