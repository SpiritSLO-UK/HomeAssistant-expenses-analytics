"""Global search across transactions, vendors, categories and projects.

Transactions are scoped to the caller's visible accounts (and exclude archived
rows), so search can never reveal a private transaction the user couldn't already
see. Library entities (vendors/categories/projects) are household-wide.

Beyond plain free-text (and the amount match, backlog #257) the query understands
a few optional **filter tokens**, mixed anywhere into the text and stripped from
the free-text portion before the normal match runs:

* ``tag:``-free tag search — a bare word that matches a transaction TAG name
  returns those transactions too (joined into the transaction match, scoped and
  archived-excluded exactly like description/merchant matches).
* ``category:<name>`` — restrict transactions to the category with that name
  (case-insensitive exact match; a name that matches nothing restricts to none).
* ``after:<date>`` / ``before:<date>`` — lower / upper bound on the transaction
  date (both inclusive).
* ``<date>..<date>`` — an inclusive date range, e.g. ``2026-01-01..2026-03-31``.

A ``<date>`` is ``YYYY-MM-DD`` or ``YYYY-MM`` (a whole month: the month's first
day for a lower bound, its last day for an upper bound). An unrecognised
``word:value`` that is not one of these tokens is left untouched as plain text, so
existing token-free queries behave exactly as before.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.models import Category, Project, Tag, Transaction, Vendor, transaction_tags
from app.services.scope import account_scope_condition, archived_condition

MIN_QUERY = 2


# Currency symbols (and thousands commas) a user might type around an amount.
_AMOUNT_STRIP = str.maketrans("", "", "£$€,")

# A single date token: YYYY-MM-DD or YYYY-MM (kept deliberately simple).
_DATE_RE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")


def _amount(q: str) -> Decimal | None:
    try:
        return abs(Decimal(q.translate(_AMOUNT_STRIP).strip()))
    except (InvalidOperation, ValueError):
        return None


def _span(value: str) -> tuple[date, date] | None:
    """Parse a date token into the ``(first, last)`` days it spans.

    ``YYYY-MM-DD`` spans a single day; ``YYYY-MM`` spans the whole month (first
    day .. last day). Returns ``None`` when ``value`` is not a valid date token.
    """
    if not _DATE_RE.match(value):
        return None
    parts = value.split("-")
    year, month = int(parts[0]), int(parts[1])
    if not 1 <= month <= 12:
        return None
    if len(parts) == 3:
        try:
            day = date(year, month, int(parts[2]))
        except ValueError:
            return None
        return day, day
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _match_date_token(word: str) -> tuple[date | None, date | None, bool]:
    """Interpret ``word`` as a date filter. Returns ``(from, to, consumed)``; when
    ``consumed`` is False the word is ordinary text and both bounds are ``None``."""
    if ".." in word:
        lo, _, hi = word.partition("..")
        lo_span, hi_span = _span(lo), _span(hi)
        if lo_span and hi_span:
            return lo_span[0], hi_span[1], True
        return None, None, False
    key, sep, value = word.partition(":")
    key = key.lower()
    if sep and key in ("after", "before"):
        span = _span(value)
        if span:
            return (span[0], None, True) if key == "after" else (None, span[1], True)
    return None, None, False


def _match_category_token(db: Session, word: str) -> list[int] | None:
    """If ``word`` is a ``category:<name>`` token, return the matching category ids
    (possibly empty → restrict to nothing). Otherwise ``None`` (ordinary text)."""
    key, sep, value = word.partition(":")
    if not sep or key.lower() != "category" or not value:
        return None
    return list(
        db.scalars(
            select(Category.id).where(Category.name.ilike(value), Category.is_active.is_(True))
        ).all()
    )


def _extract_filters(db: Session, query: str) -> tuple[str, list[ColumnElement[bool]]]:
    """Split ``query`` into (free_text, filter_conditions), consuming recognised
    tokens and leaving everything else as free text."""
    kept: list[str] = []
    conditions: list[ColumnElement[bool]] = []
    date_from: date | None = None
    date_to: date | None = None
    for word in query.split():
        d_from, d_to, consumed = _match_date_token(word)
        if consumed:
            date_from = d_from or date_from
            date_to = d_to or date_to
            continue
        cat_ids = _match_category_token(db, word)
        if cat_ids is not None:
            conditions.append(Transaction.category_id.in_(cat_ids))
            continue
        kept.append(word)
    if date_from is not None:
        conditions.append(Transaction.transaction_date >= date_from)
    if date_to is not None:
        conditions.append(Transaction.transaction_date <= date_to)
    return " ".join(kept), conditions


def _tag_match_subquery(text_q: str):
    """Transaction ids whose tag name matches ``text_q`` (case-insensitive
    substring), for OR-ing into the transaction match."""
    return (
        select(transaction_tags.c.transaction_id)
        .join(Tag, Tag.id == transaction_tags.c.tag_id)
        .where(Tag.name.ilike(f"%{text_q}%"))
        .scalar_subquery()
    )


def _txn_or_conditions(text_q: str) -> list[ColumnElement[bool]]:
    """The OR-group of free-text transaction matches (description/merchant, tag
    name, and amount). Empty when there is no free text (filter-only query)."""
    if not text_q:
        return []
    from app.db import search_index

    conds: list[ColumnElement[bool]] = []
    if search_index.use_fts(text_q):
        # Index-backed substring search across description + merchant (backlog #43).
        conds.append(Transaction.id.in_(search_index.match_subquery(text_q)))
    else:
        like = f"%{text_q}%"
        conds.append(Transaction.description_raw.ilike(like))
        conds.append(Transaction.merchant_raw.ilike(like))
    # Tag-name match (join tags into the transaction match).
    conds.append(Transaction.id.in_(_tag_match_subquery(text_q)))
    amount = _amount(text_q)
    if amount is not None:
        # Match either the original-currency amount (as seen on the statement) or
        # the base-currency amount (as seen on the dashboard) — base_amount is NULL
        # for needs-rate rows, which simply won't match.
        conds.append(func.abs(Transaction.amount) == amount)
        conds.append(func.abs(Transaction.base_amount) == amount)
    return conds


def search(db: Session, query: str, *, account_ids: set[int] | None, limit: int = 8) -> dict:
    q = (query or "").strip()
    result: dict = {"query": q, "transactions": [], "vendors": [], "categories": [], "projects": []}
    if len(q) < MIN_QUERY:
        return result

    text_q, filter_conditions = _extract_filters(db, q)
    # Nothing to go on: too-short free text and no filters (preserves the existing
    # short-query behaviour; a filter-only query with empty text is still valid).
    if len(text_q) < MIN_QUERY and not filter_conditions:
        return result

    where: list[ColumnElement[bool]] = [
        *account_scope_condition(account_ids),
        *archived_condition(),
        *filter_conditions,
    ]
    or_conditions = _txn_or_conditions(text_q)
    if or_conditions:
        where.append(or_(*or_conditions))

    txns = db.scalars(
        select(Transaction)
        .where(*where)
        .order_by(Transaction.transaction_date.desc())
        .limit(limit)
    ).all()
    result["transactions"] = [
        {
            "id": t.id,
            "transaction_date": t.transaction_date.isoformat(),
            "description": t.description_raw,
            "amount": str(t.amount),
            "currency": t.currency,
        }
        for t in txns
    ]

    # Library entities are matched on the free-text portion only; a filter-only
    # query (no free text) leaves them empty rather than dumping everything.
    if not text_q:
        return result
    like = f"%{text_q}%"
    result["vendors"] = [
        {"id": v.id, "name": v.canonical_name}
        for v in db.scalars(
            select(Vendor).where(Vendor.canonical_name.ilike(like)).order_by(Vendor.canonical_name).limit(limit)
        ).all()
    ]
    result["categories"] = [
        {"id": c.id, "name": c.name, "colour": c.colour}
        for c in db.scalars(
            select(Category)
            .where(Category.name.ilike(like), Category.is_active.is_(True))
            .order_by(Category.name)
            .limit(limit)
        ).all()
    ]
    result["projects"] = [
        {"id": p.id, "name": p.name, "status": p.status}
        for p in db.scalars(
            select(Project).where(Project.name.ilike(like)).order_by(Project.name).limit(limit)
        ).all()
    ]
    return result
