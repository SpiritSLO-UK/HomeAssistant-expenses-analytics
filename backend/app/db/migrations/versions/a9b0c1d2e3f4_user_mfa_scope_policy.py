"""users.mfa_scope + users.mfa_policy (MFA scope + admin enforcement, #157)

mfa_scope  — what the user's MFA gates: "app" (a code on app open only) or
             "app_admin" (also a step-up for admin actions). Default preserves the
             existing behaviour (both).
mfa_policy — admin enforcement: "optional" (default) or "required" (the user is
             blocked from the app until they enrol in MFA).

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-06-06 12:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a9b0c1d2e3f4'
down_revision: str | None = 'f8a9b0c1d2e3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('mfa_scope', sa.String(length=16), nullable=False, server_default='app_admin'))
    op.add_column('users', sa.Column('mfa_policy', sa.String(length=16), nullable=False, server_default='optional'))


def downgrade() -> None:
    op.drop_column('users', 'mfa_policy')
    op.drop_column('users', 'mfa_scope')
