"""users.blocked_nav (admin-set per-user blocked pages)

Lets the owner restrict which app pages a non-admin user can reach — enforced
server-side (the auth guard 403s the page's API) and hidden in the sidebar
(backlog #108). Stored as a JSON array of nav-page keys; NULL/empty = unrestricted.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-06-06 11:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f8a9b0c1d2e3'
down_revision: str | None = 'e7f8a9b0c1d2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('blocked_nav', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'blocked_nav')
