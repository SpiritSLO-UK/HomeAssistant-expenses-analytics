"""Account-visibility filter helper (shared vs private accounts; backlog #66/#82).

A single place that turns an ``account_ids`` scope into a SQLAlchemy condition,
so every aggregate over transactions filters identically.

CRITICAL: the guard is ``account_ids is not None``. ``None`` means *unrestricted*
(the owner/admin fast path) → no condition. An **empty set** means *nothing
visible* → ``account_id IN () OR account_id IS NULL`` (only orphans), NOT "all
rows". Confusing the two would invert the whole privacy model into a leak.
"""

from __future__ import annotations

from sqlalchemy import or_

from app.models import Transaction


def account_scope_condition(account_ids: set[int] | None) -> list:
    """Return ``[condition]`` to splat into a ``.where(...)``, or ``[]`` when
    unrestricted. Orphan transactions (``account_id IS NULL``, e.g. a deleted
    account) are treated as shared and stay visible."""
    if account_ids is None:
        return []
    return [or_(Transaction.account_id.in_(account_ids), Transaction.account_id.is_(None))]


def archived_condition(include_archived: bool = False) -> list:
    """Return ``[condition]`` excluding archived transactions, or ``[]`` to include
    them. Archived rows (aged out by retention, backlog #78) are hidden from every
    aggregate and from the default transactions list; only the list/CSV expose an
    opt-in toggle so a user can find and restore them."""
    return [] if include_archived else [Transaction.archived_at.is_(None)]
