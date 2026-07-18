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
HUNDRED = Decimal("100")
# A set of percentages may miss 100 by a rounding hair (e.g. 33.3 * 3 = 99.9).
# Accept anything within this band of 100; the penny-exact distribution below
# still makes the parts sum to the parent to the cent regardless.
PERCENT_TOLERANCE = Decimal("0.5")


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


@dataclass
class PercentInput:
    """One proposed percentage split part (its share of the transaction, 0–100)."""

    percent: Decimal
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


def split_evenly(total: Decimal | str | float | int, n: int) -> list[Decimal]:
    """Divide ``total`` into ``n`` penny-exact parts (SR-A5, mirrors the frontend
    "Split evenly" from PR #44).

    Uses integer-cents math so the parts always sum back to ``total`` to the
    penny, with any odd pennies spread one-each across the first parts (rather
    than dumped in the last one). The parts keep the sign of ``total``.

    Example: ``split_evenly("10.00", 3) == [3.34, 3.33, 3.33]``.

    Raises :class:`SplitError` if ``n < 2`` (a split needs at least two parts).
    """
    if n < 2:
        raise SplitError("A split needs at least two parts.")
    # Quantise to the penny then work in signed integer cents to stay exact.
    total_cents = int((_q(total) * 100).to_integral_value())
    sign = -1 if total_cents < 0 else 1
    magnitude = abs(total_cents)
    base, remainder = divmod(magnitude, n)
    # The first ``remainder`` parts get one extra penny.
    cents = [base + 1 if i < remainder else base for i in range(n)]
    return [Decimal(sign * c) / 100 for c in cents]


def _percent_amounts(total: Decimal, percents: list[Decimal]) -> list[Decimal]:
    """Split ``total`` across the given ``percents`` (each a share of 100),
    penny-exact so the parts sum back to ``total`` to the cent.

    Works in signed integer cents. Each part's ideal cents is its share of the
    magnitude, normalised by the percentages' actual sum (so a set that misses
    100 by a rounding hair still spends the whole total). We floor those and hand
    the leftover pennies to the parts with the largest fractional remainder (the
    same largest-remainder rule ``_distributed_base_amounts`` uses), so no cent
    drifts. Parts keep the sign of ``total``.
    """
    total_cents = int((total * 100).to_integral_value())
    sign = -1 if total_cents < 0 else 1
    magnitude = abs(total_cents)
    denom = sum(percents)  # normalise by the real sum, not a bare 100
    ideals = [Decimal(magnitude) * p / denom for p in percents]
    floors = [int(x) for x in ideals]  # non-negative, so int() floors
    leftover = magnitude - sum(floors)
    # Largest fractional remainder first; break ties by earliest part.
    order = sorted(range(len(percents)), key=lambda i: (ideals[i] - floors[i], -i), reverse=True)
    cents = floors[:]
    for i in order[:leftover]:
        cents[i] += 1
    return [Decimal(sign * c) / 100 for c in cents]


def split_by_percentages(
    total: Decimal | str | float | int, parts: list[PercentInput]
) -> list[SplitInput]:
    """Split ``total`` across ``parts`` by percentage, returning ready-to-store
    :class:`SplitInput` rows (category/project/description/notes carried through).

    The percentages must sum to 100 (within :data:`PERCENT_TOLERANCE`); the
    resulting ``amount``\\ s are penny-exact against ``total`` via
    :func:`_percent_amounts`, so callers can hand the rows straight to
    :func:`set_splits` without introducing cent drift. Mirrors
    :func:`split_evenly`'s style (pure computation; validation happens there).

    Raises :class:`SplitError` for fewer than two parts, a non-positive
    percentage, or a percentage sum that misses 100 by more than the tolerance.
    """
    if len(parts) < 2:
        raise SplitError("A split needs at least two parts.")
    percents = [Decimal(str(p.percent)) for p in parts]
    if any(p <= 0 for p in percents):
        raise SplitError("Each percentage must be greater than zero.")
    pct_sum = sum(percents)
    if abs(pct_sum - HUNDRED) > PERCENT_TOLERANCE:
        raise SplitError(f"Percentages sum to {pct_sum}, not 100.")
    amounts = _percent_amounts(_q(total), percents)
    return [
        SplitInput(
            amount=amount,
            category_id=part.category_id,
            project_id=part.project_id,
            description=part.description,
            notes=part.notes,
        )
        for amount, part in zip(amounts, parts)
    ]


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
    # An archived transaction is hidden from every aggregate (backlog #78), so
    # splitting it would silently produce category rows that never surface; and a
    # transfer is not spend/income at all, so it has nothing meaningful to split
    # across categories. Reject both up front (SR-A5).
    if txn.archived_at is not None:
        raise SplitError("Cannot split an archived transaction.")
    if txn.is_transfer:
        raise SplitError("Cannot split a transfer transaction.")

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


def _distributed_base_amounts(txn: Transaction) -> dict[int, Decimal] | None:
    """Base-currency contribution of each of ``txn``'s splits, keyed by the
    split's Python ``id()``, distributed so the parts sum **exactly** to the
    transaction's stored ``base_amount`` (no penny drift — SR-A5).

    Each part is ``split.amount * fx_rate`` quantised to the penny, which can
    accumulate a cent of rounding error against the parent total. We compute the
    drift versus the parent's ``base_amount`` (the source of truth ``summary``
    uses) and hand out the odd pennies to the parts that were rounded furthest,
    so the category breakdown / budgets / projects agree with the summary.
    Returns ``None`` when the transaction has no rate yet (``needs_rate``).
    """
    if txn.fx_rate is None:
        return None
    raws = [(s, s.amount * txn.fx_rate) for s in txn.splits]
    rounded = {id(s): raw.quantize(TWO_DP) for s, raw in raws}
    target = txn.base_amount
    if target is None or not raws:
        return rounded
    drift = target - sum(rounded.values())
    if drift != 0:
        steps = int(abs(drift) / TWO_DP)
        step = TWO_DP if drift > 0 else -TWO_DP
        # Give the odd pennies to the parts rounded furthest in the drift's
        # direction (largest residual first), so the adjustment is least visible.
        order = sorted(raws, key=lambda sr: sr[1] - rounded[id(sr[0])], reverse=drift > 0)
        for s, _ in order[:steps]:
            rounded[id(s)] += step
    return rounded


def split_base_amount(txn: Transaction, split: TransactionSplit) -> Decimal | None:
    """The split's contribution in the household base currency (spec §37.4).

    A split carries only its original-currency ``amount``; we reuse the parent
    transaction's FX rate (1.0 for same-currency rows). Returns ``None`` when the
    transaction has no rate yet (``needs_rate``), so callers can exclude it the
    same way they exclude the parent (backlog #29). The amount is penny-exact
    against the parent's stored ``base_amount`` (see ``_distributed_base_amounts``).
    """
    amounts = _distributed_base_amounts(txn)
    if amounts is None:
        return None
    return amounts.get(id(split))
