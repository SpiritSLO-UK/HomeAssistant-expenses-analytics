"""Transaction split service (spec §17, §12.7).

A split divides one transaction across several categories and/or projects. The
bank transaction stays the source of truth (spec §12.7); splits drive the
dashboard category breakdown (spec §37.4) once present.

Validation (spec §17.2):
  - the split total must equal the transaction total, to the penny;
  - every split must set a category and/or a project;
  - all split amounts must share the transaction's sign (no mixing debit/credit);
  - a split has at least two parts (one part is just the transaction itself).

Currency is implicit: a split has no currency of its own, so it always matches
the parent transaction (spec §17.2 "currency must match").
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models import Category, Project, Transaction, TransactionSplit

TWO_DP = Decimal("0.01")


class SplitError(ValueError):
    """A proposed set of splits is invalid (spec error ``split_invalid``, §36)."""


@dataclass
class SplitInput:
    """One proposed split part, as received from the API."""

    amount: Decimal
    category_id: int | None = None
    project_id: int | None = None
    description: str | None = None
    notes: str | None = None


def _q(value: Decimal | str | float) -> Decimal:
    """Quantise to 2 decimal places (money), tolerating str/float inputs."""
    try:
        return Decimal(str(value)).quantize(TWO_DP)
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - defensive
        raise SplitError(f"Invalid amount: {value!r}") from exc


def _validate_part(db: Session, i: int, part: SplitInput, *, txn_negative: bool) -> SplitInput:
    """Validate one split part (spec §17.2) and return its quantised copy.

    Raises :class:`SplitError` with the same messages as the inline checks did.
    """
    amount = _q(part.amount)
    if amount == 0:
        raise SplitError(f"Split {i} has a zero amount.")
    if (amount < 0) != txn_negative:
        sign = "negative (debit)" if txn_negative else "positive (credit)"
        raise SplitError(f"Split {i} must be {sign}, to match the transaction.")
    if part.category_id is None and part.project_id is None:
        raise SplitError(f"Split {i} needs a category and/or a project.")
    if part.category_id is not None and db.get(Category, part.category_id) is None:
        raise SplitError(f"Split {i} references an unknown category.")
    if part.project_id is not None and db.get(Project, part.project_id) is None:
        raise SplitError(f"Split {i} references an unknown project.")
    return SplitInput(
        amount=amount,
        category_id=part.category_id,
        project_id=part.project_id,
        description=part.description,
        notes=part.notes,
    )


def validate(db: Session, txn: Transaction, parts: list[SplitInput]) -> list[SplitInput]:
    """Validate ``parts`` against ``txn`` (spec §17.2). Returns the quantised
    parts on success; raises :class:`SplitError` otherwise."""
    if len(parts) < 2:
        raise SplitError("A split needs at least two parts.")

    txn_total = _q(txn.amount)
    if txn_total == 0:
        raise SplitError("Cannot split a zero-amount transaction.")
    txn_negative = txn_total < 0

    cleaned: list[SplitInput] = []
    running = Decimal("0.00")
    for i, part in enumerate(parts, start=1):
        clean = _validate_part(db, i, part, txn_negative=txn_negative)
        running += clean.amount
        cleaned.append(clean)

    if running != txn_total:
        raise SplitError(
            f"Splits total {running} but the transaction is {txn_total} "
            f"(off by {(txn_total - running)})."
        )
    return cleaned


def set_splits(db: Session, txn: Transaction, parts: list[SplitInput]) -> Transaction:
    """Replace ``txn``'s splits with ``parts`` after validation, and flag the
    transaction as split (spec §17). Commits."""
    cleaned = validate(db, txn, parts)

    # Replace wholesale; cascade="all, delete-orphan" removes the old rows.
    txn.splits.clear()
    db.flush()
    for part in cleaned:
        txn.splits.append(
            TransactionSplit(
                category_id=part.category_id,
                project_id=part.project_id,
                amount=part.amount,
                description=part.description,
                notes=part.notes,
            )
        )
    txn.is_split = True
    db.commit()
    db.refresh(txn)
    return txn


def clear_splits(db: Session, txn: Transaction) -> Transaction:
    """Remove all splits and clear the split flag (spec §17.3 "remove split").
    The transaction's own category/project become the source of truth again."""
    txn.splits.clear()
    txn.is_split = False
    db.commit()
    db.refresh(txn)
    return txn


def split_base_amount(txn: Transaction, split: TransactionSplit) -> Decimal | None:
    """The split's contribution in the household base currency (spec §37.4).

    A split carries only its original-currency ``amount``; we reuse the parent
    transaction's FX rate (1.0 for same-currency rows). Returns ``None`` when the
    transaction has no rate yet (``needs_rate``), so callers can exclude it the
    same way they exclude the parent (backlog #29). Penny rounding across parts
    may differ from the transaction's stored ``base_amount`` by a cent.
    """
    if txn.fx_rate is None:
        return None
    return (split.amount * txn.fx_rate).quantize(TWO_DP)
