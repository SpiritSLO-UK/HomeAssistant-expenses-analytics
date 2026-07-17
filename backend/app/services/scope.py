"""Account-visibility filter helper (shared vs private accounts; backlog #66/#82).

A single place that turns an ``account_ids`` scope into a SQLAlchemy condition,
so every aggregate over transactions filters identically.

CRITICAL: the guard is ``account_ids is not None``. ``None`` means *unrestricted*
(the owner/admin fast path) → no condition, so owners see every row including any
orphan. A **restricted set** (any ``set``, empty or not) means the member is
confined to exactly those account ids: the predicate is ``account_id IN (<set>)``
and nothing else. An **empty set** therefore matches *nothing* (SQLAlchemy renders
an empty ``IN`` as an always-false expression), NOT "all rows". Confusing the two
would invert the whole privacy model into a leak.

ORPHAN TRANSACTIONS (``account_id IS NULL``): a transaction with no account is
distinct from an *unowned* account (``Account.owner_user_id IS NULL``, which has a
real id and IS included in every member's visible set, so it stays visible). True
orphans are theoretical — deleting an account requires it be empty — but if one
ever arises it is visible to **owners/admins only** (they use the ``None``
fast-path and see everything). Restricted members never see orphans: they fall
outside every member's account-id set, and we deliberately do NOT add an
``OR account_id IS NULL`` clause. This is the conservative rule (owner-only, never
leaked to members) and keeps owners able to find and re-assign a stray row.
"""

from __future__ import annotations

from app.models import Transaction


def account_scope_condition(account_ids: set[int] | None) -> list:
    """Return ``[condition]`` to splat into a ``.where(...)``, or ``[]`` when
    unrestricted.

    ``account_ids is None`` (owner/admin) → no condition; owners see every row,
    including any orphan transaction (``account_id IS NULL``). A restricted set
    (empty or not) confines the member to ``account_id IN (<set>)`` — orphans are
    NOT matched, so they stay owner-visible only. An empty set matches nothing (an
    empty SQL ``IN`` is always false), never everything.
    """
    if account_ids is None:
        return []
    # Restricted: exactly the member's account ids. Orphans (account_id IS NULL)
    # are intentionally excluded — owner-only. An empty set → always-false IN.
    return [Transaction.account_id.in_(account_ids)]


def archived_condition(include_archived: bool = False) -> list:
    """Return ``[condition]`` excluding archived transactions, or ``[]`` to include
    them. Archived rows (aged out by retention, backlog #78) are hidden from every
    aggregate and from the default transactions list; only the list/CSV expose an
    opt-in toggle so a user can find and restore them."""
    return [] if include_archived else [Transaction.archived_at.is_(None)]
