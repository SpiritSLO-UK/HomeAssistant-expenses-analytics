"""Curve funding-card links + cross-account dedup (user ask: Curve is a transient
overlay card).

Curve forwards each payment to an underlying funding card, so the same spend also
lands on that card's own statement. Importing both the Curve export *and* the
underlying card's statement would otherwise double-count, and the per-account
``source_hash`` dedup can't catch it (different account, different description,
settlement-date lag).

The user maps each Curve funding-card label (``Card Name ••1234``) to the real
account behind it (:class:`CurveFundingLink`). On import we then look for the
same spend across the two accounts:

* same currency and exact (signed) amount,
* transaction dates within :data:`DATE_WINDOW_DAYS`,
* scoped to the *mapped* account only.

A match is only auto-skipped when the **bank-side** description carries a Curve
marker (``CURVE`` / ``CRV*…`` — how banks label a Curve settlement). An
amount+date match without that marker is reported as a *possible* duplicate but
kept (flagged ``needs_review``), to keep false positives from silently dropping a
genuinely different same-price purchase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Account, CurveFundingLink, Transaction
from app.parsers.base import StandardTransaction
from app.services.household_service import get_or_create_default_household

# How far apart the two statements' dates may be (settlement lag).
DATE_WINDOW_DAYS = 4
# How banks tend to label a Curve settlement on the underlying card.
CURVE_MARKERS = ("curve", "crv")

_CENTS = Decimal("0.01")


@dataclass(frozen=True)
class CrossMatch:
    """A parsed row that also appears on the linked funding account."""

    account_id: int
    account_name: str
    other_date: date
    other_description: str
    confidence: str  # "high" (Curve-marked → skip) | "low" (flag, keep)

    @property
    def reason(self) -> str:
        when = self.other_date.isoformat()
        if self.confidence == "high":
            return f"Also on {self.account_name} · {when}"
        return f"Possible match on {self.account_name} · {when} (no Curve marker)"


# --- Link CRUD -------------------------------------------------------------

def list_links(db: Session) -> list[CurveFundingLink]:
    household = get_or_create_default_household(db)
    return list(
        db.scalars(
            select(CurveFundingLink)
            .where(CurveFundingLink.household_id == household.id)
            .order_by(CurveFundingLink.label)
        ).all()
    )


def _find_link(db: Session, household_id: int | None, label: str) -> CurveFundingLink | None:
    """Locate a link by case-insensitive label within the household."""
    return db.scalars(
        select(CurveFundingLink).where(
            CurveFundingLink.household_id == household_id,
            func.lower(CurveFundingLink.label) == label.strip().lower(),
        )
    ).first()


def set_link(db: Session, label: str, account_id: int | None) -> CurveFundingLink | None:
    """Map ``label`` to ``account_id``; ``account_id=None`` clears the mapping.

    Returns the saved link, or None when cleared. Raises ValueError on an unknown
    account.
    """
    label = (label or "").strip()
    if not label:
        raise ValueError("Label is required")
    household = get_or_create_default_household(db)
    existing = _find_link(db, household.id, label)
    if account_id is None:
        if existing is not None:
            db.delete(existing)
            db.commit()
        return None
    if db.get(Account, account_id) is None:
        raise ValueError("Unknown account")
    if existing is None:
        existing = CurveFundingLink(household_id=household.id, label=label, account_id=account_id)
        db.add(existing)
    else:
        existing.account_id = account_id
    db.commit()
    db.refresh(existing)
    return existing


def link_map(db: Session) -> dict[str, int]:
    """Case-insensitive ``label → account_id`` for the household."""
    return {link.label.lower(): link.account_id for link in list_links(db)}


def funding_labels_for_rows(db: Session, rows: list[StandardTransaction]) -> list[dict]:
    """Distinct funding-card labels in ``rows`` with counts + current mapping.

    Drives the Import page's mapping panel. Empty for ordinary statements (no row
    carries a ``funding_source``)."""
    counts: dict[str, int] = {}
    for r in rows:
        if r.funding_source:
            counts[r.funding_source] = counts.get(r.funding_source, 0) + 1
    if not counts:
        return []
    mapping = link_map(db)
    account_names = {a.id: a.name for a in db.scalars(select(Account)).all()}
    out = []
    for label in sorted(counts):
        acc_id = mapping.get(label.lower())
        out.append(
            {
                "label": label,
                "count": counts[label],
                "account_id": acc_id,
                "account_name": account_names.get(acc_id) if acc_id else None,
            }
        )
    return out


# --- Cross-account matching ------------------------------------------------

def _q2(value: Decimal) -> Decimal:
    return Decimal(value).quantize(_CENTS)


def _has_curve_marker(*texts: str | None) -> bool:
    for text in texts:
        if text:
            low = text.lower()
            if any(m in low for m in CURVE_MARKERS):
                return True
    return False


def _date_range(rows: list[StandardTransaction]) -> tuple[date, date] | None:
    dates = [r.transaction_date for r in rows]
    if not dates:
        return None
    pad = timedelta(days=DATE_WINDOW_DAYS)
    return min(dates) - pad, max(dates) + pad


def detect_cross_account(
    db: Session,
    *,
    target_account_id: int,
    parsed_rows: list[StandardTransaction],
    links: dict[str, int],
) -> dict[int, CrossMatch]:
    """Find rows that also appear on a linked funding account.

    Returns ``{row_index: CrossMatch}``. Handles both import directions:

    * importing the **Curve** export (rows carry ``funding_source``) → match
      against the mapped underlying account's existing transactions;
    * importing the **underlying card** (no ``funding_source``) → match against
      existing Curve transactions whose label maps to this account.
    """
    if not links or not parsed_rows:
        return {}
    window = _date_range(parsed_rows)
    if window is None:
        return {}
    lo, hi = window
    account_names = {a.id: a.name for a in db.scalars(select(Account)).all()}

    # Direction A: which underlying accounts do this file's rows map to?
    mapped_accounts = {
        links[r.funding_source.lower()]
        for r in parsed_rows
        if r.funding_source and r.funding_source.lower() in links
    }
    mapped_accounts.discard(target_account_id)
    # Direction B: Curve labels that point at the account we're importing into.
    labels_for_target = {label for label, acc in links.items() if acc == target_account_id}

    candidates_by_account = _candidates_by_account(db, mapped_accounts, lo, hi)
    curve_candidates = _curve_candidates(db, labels_for_target, lo, hi)

    used: set[int] = set()  # an existing txn matches at most one incoming row
    matches: dict[int, CrossMatch] = {}
    for i, row in enumerate(parsed_rows):
        match = _match_row(
            row, target_account_id, links, labels_for_target,
            candidates_by_account, curve_candidates, used, account_names,
        )
        if match is not None:
            matches[i] = match
    return matches


def _candidates_by_account(
    db: Session, mapped_accounts: set[int], lo: date, hi: date
) -> dict[int, list[Transaction]]:
    """Existing transactions in the mapped underlying accounts within the window."""
    out: dict[int, list[Transaction]] = {}
    if not mapped_accounts:
        return out
    for txn in db.scalars(
        select(Transaction).where(
            Transaction.account_id.in_(mapped_accounts),
            Transaction.transaction_date >= lo,
            Transaction.transaction_date <= hi,
            Transaction.archived_at.is_(None),
        )
    ).all():
        out.setdefault(txn.account_id, []).append(txn)
    return out


def _curve_candidates(
    db: Session, labels_for_target: set[str], lo: date, hi: date
) -> list[Transaction]:
    """Existing Curve transactions whose funding label maps to the target account."""
    if not labels_for_target:
        return []
    return list(
        db.scalars(
            select(Transaction).where(
                func.lower(Transaction.funding_source).in_(labels_for_target),
                Transaction.transaction_date >= lo,
                Transaction.transaction_date <= hi,
                Transaction.archived_at.is_(None),
            )
        ).all()
    )


def _match_row(
    row: StandardTransaction,
    target_account_id: int,
    links: dict[str, int],
    labels_for_target: set[str],
    candidates_by_account: dict[int, list[Transaction]],
    curve_candidates: list[Transaction],
    used: set[int],
    account_names: dict[int, str],
) -> CrossMatch | None:
    """Match one incoming row against the right candidate set for its direction."""
    if row.funding_source and row.funding_source.lower() in links:
        acc_id = links[row.funding_source.lower()]
        if acc_id == target_account_id:
            return None
        # Direction A: bank-side is the existing candidate in that account.
        return _match_against(
            row, candidates_by_account.get(acc_id, []), used, account_names, bank_side="candidate",
        )
    if not row.funding_source and labels_for_target:
        # Direction B: bank-side is the incoming row itself.
        return _match_against(row, curve_candidates, used, account_names, bank_side="row")
    return None


def _match_against(
    row: StandardTransaction,
    candidates: list[Transaction],
    used: set[int],
    account_names: dict[int, str],
    *,
    bank_side: str,
) -> CrossMatch | None:
    amount = _q2(row.amount)
    for cand in candidates:
        if cand.id in used:
            continue
        if cand.currency != row.currency or _q2(cand.amount) != amount:
            continue
        if abs((cand.transaction_date - row.transaction_date).days) > DATE_WINDOW_DAYS:
            continue
        used.add(cand.id)
        if bank_side == "candidate":
            marked = _has_curve_marker(cand.description_raw, cand.merchant_raw)
        else:  # the incoming row is the bank statement
            marked = _has_curve_marker(row.description_raw, row.merchant_raw)
        return CrossMatch(
            account_id=cand.account_id,
            account_name=account_names.get(cand.account_id, "another account"),
            other_date=cand.transaction_date,
            other_description=cand.description_raw,
            confidence="high" if marked else "low",
        )
    return None
