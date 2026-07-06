"""tags case-insensitive unique per household (SR-B8)

Two concurrent get_or_create calls could create case-insensitive duplicate tags
("Work" and "work") because uniqueness was only enforced in Python. This dedupes
any existing case-insensitive duplicates within a household (repointing
transaction_tags to the surviving row, then deleting the losers) and adds a
functional UNIQUE index on (COALESCE(household_id, -1), lower(name)) so duplicates
can't recur. household_id is nullable and unique indexes treat NULLs as distinct,
so the COALESCE folds the "no household" rows into one scope. The index name +
expression match the Tag model's __table_args__ so create_all (tests) and Alembic
(runtime) agree.

Revision ID: c5d6e7f8a9b0
Revises: f9a0b1c2d3e4
Create Date: 2026-07-06 00:00:00.000000
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c5d6e7f8a9b0'
down_revision: str | None = 'f9a0b1c2d3e4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_tags_household_lower_name"


def upgrade() -> None:
    # 1. Repoint the association table off the duplicate tag rows onto the survivor
    #    (the lowest id per (household, lower(name)) group) so no tag links are
    #    orphaned when we delete the losers.
    #
    #    A plain UPDATE would trip the association PK (transaction_id, tag_id) the
    #    moment a transaction already linked to the survivor, so instead we
    #    INSERT-OR-IGNORE the survivor link for every loser link (the IGNORE
    #    swallows the collision when the survivor link already exists), then delete
    #    all the loser links. The "loser" set is any tag that is not the MIN(id) of
    #    its (household, lower(name)) group.
    op.execute(
        """
        INSERT OR IGNORE INTO transaction_tags (transaction_id, tag_id)
        SELECT tt.transaction_id, survivor.id
        FROM transaction_tags tt
        JOIN tags loser ON loser.id = tt.tag_id
        JOIN (
            SELECT MIN(id) AS id, COALESCE(household_id, -1) AS hh, lower(name) AS lname
            FROM tags GROUP BY COALESCE(household_id, -1), lower(name)
        ) survivor
          ON survivor.hh = COALESCE(loser.household_id, -1)
         AND survivor.lname = lower(loser.name)
        WHERE loser.id <> survivor.id
        """
    )
    op.execute(
        """
        DELETE FROM transaction_tags
        WHERE tag_id NOT IN (
            SELECT MIN(id) FROM tags GROUP BY COALESCE(household_id, -1), lower(name)
        )
        """
    )
    # 2. Delete the now-unreferenced duplicate tag rows (keep the lowest id).
    op.execute(
        """
        DELETE FROM tags
        WHERE id NOT IN (
            SELECT MIN(id) FROM tags GROUP BY COALESCE(household_id, -1), lower(name)
        )
        """
    )
    # 3. Enforce it at the DB level going forward.
    op.execute(
        f"CREATE UNIQUE INDEX {_INDEX} "
        "ON tags (COALESCE(household_id, -1), lower(name))"
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="tags")
