"""users.nav_layout (per-user customised grouped sidebar layout, grouped-nav PR1/4)

Lets each user persist their own sidebar layout (groups -> items, with
rename/hide/reorder/move/custom-groups). Stored as a JSON object; NULL = use the
built-in default layout. Set self-service via PUT /api/users/me/nav-layout.

Revision ID: a1c2e3d4f5b6
Revises: d3e4f5a6b7c8
Create Date: 2026-07-19 09:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1c2e3d4f5b6'
down_revision: str | None = 'd3e4f5a6b7c8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('nav_layout', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'nav_layout')
