"""user.mfa_pending_secret — hold an unconfirmed (re-)enrolment secret

A not-yet-confirmed TOTP secret lives here during (re-)enrolment so the live
``mfa_secret`` / ``mfa_enabled`` stay intact until the user proves a code for the
new secret. Closes the un-authenticated MFA downgrade where ``start_enrolment``
cleared ``mfa_enabled`` outright (SR-1).

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-06-27 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e3f4a5b6c7d8'
down_revision: str | None = 'd2e3f4a5b6c7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('mfa_pending_secret', sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'mfa_pending_secret')
