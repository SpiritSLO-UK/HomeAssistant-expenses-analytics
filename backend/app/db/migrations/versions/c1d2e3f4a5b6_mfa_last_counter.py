"""users.mfa_last_counter (TOTP one-time-use / anti-replay)

Records the highest TOTP timestep already accepted on the app-entry path so a
valid code can't be replayed within its ~90s window to mint a second session
(CR-SEC-5). Nullable: NULL = no code consumed yet; reset on (re-)enrolment.

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
Create Date: 2026-06-27 00:00:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: str | None = 'b0c1d2e3f4a5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('mfa_last_counter', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'mfa_last_counter')
