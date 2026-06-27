"""import_profiles — saved CSV column-mapping profiles

Lets a user save a reusable CSV column mapping (logical field -> header) so an
unsupported bank's statement imports in one click next time, and so the mapping
can be exported/shared. Backlog: user-defined CSV import.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-06-27 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: str | None = 'c1d2e3f4a5b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'import_profiles',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('household_id', sa.Integer(), sa.ForeignKey('households.id', ondelete='CASCADE'), nullable=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('mapping_json', sa.Text(), nullable=False),
        sa.Column('default_currency', sa.String(length=8), nullable=False, server_default='GBP'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('household_id', 'name', name='uq_import_profiles_household_name'),
    )


def downgrade() -> None:
    op.drop_table('import_profiles')
