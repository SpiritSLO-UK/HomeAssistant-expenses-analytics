"""Hand-typed transactions (manual entry).

Everything else that creates a transaction needs a document first: a statement to
import, or a receipt to photograph. That leaves no way to record a cash spend with
no receipt, or income that never appears on an imported statement. This module is
the third way in, and deliberately runs the same post-create pipeline as the other
two (FX conversion, then auto-categorisation) so a hand-typed row is an ordinary
transaction afterwards: editable, splittable, taggable and counted in every
aggregate.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from datetime import date as date_type
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Account, Category, Transaction
from app.services import fx_service, import_service, settings_service
from app.services.household_service import get_or_create_default_household


def create_manual(
    db: Session,
    *,
    account_id: int,
    description: str,
    amount: Decimal,
    direction: str,
    transaction_date: date_type | None = None,
    currency: str | None = None,
    category_id: int | None = None,
) -> Transaction:
    """Create a transaction from typed-in fields.

    ``amount`` is a positive magnitude and ``direction`` decides the sign: a debit
    is money out (stored negative), a credit is money in (stored positive and
    flagged ``is_income``). The dashboard's spend/income split keys off the sign of
    ``base_amount``, so getting this right here is what makes a manual income entry
    show up as income rather than negative spend.

    Raises ``ValueError`` for an unknown account or category, or a description
    that is blank once trimmed; the caller maps that to a 400.
    """
    description = description.strip()
    if not description:
        raise ValueError("Description is required")
    account = db.get(Account, account_id)
    if account is None:
        raise ValueError("Account not found")
    if category_id is not None and db.get(Category, category_id) is None:
        raise ValueError("Unknown category")

    household = get_or_create_default_household(db)
    base_currency = settings_service.get_base_currency(db)
    fx_mode = settings_service.get_fx_mode(db)
    is_income = direction == "credit"
    magnitude = abs(Decimal(amount))

    txn = Transaction(
        household_id=household.id,
        account_id=account.id,
        transaction_date=transaction_date or datetime.now(UTC).date(),
        description_raw=description,
        amount=magnitude if is_income else -magnitude,
        currency=(currency or base_currency or "GBP")[:3].upper(),
        direction=direction,
        is_income=is_income,
        category_id=category_id,
        # Unique per entry, like the receipt path's "receipt-txn:<id>". The import
        # dedup key is sha256(account|date|amount|currency|description|posted_date),
        # so a content-derived hash would make two identical cash spends on one day
        # (say two coffees) collide, and a later statement import could silently
        # drop a real row as a duplicate of a hand-typed one. A manual entry is an
        # assertion by the user, not something to dedupe against.
        source_hash=f"manual-txn:{uuid.uuid4().hex}",
        needs_review=False,
        # A typed category is a deliberate choice, so mark it fully confident the
        # way the manual categorise endpoint does (spec §15.2). Left unset when no
        # category was chosen, so auto_categorise below can still fill it in.
        confidence_score=1.0 if category_id is not None else None,
    )
    db.add(txn)
    db.flush()
    fx_service.convert_transaction(db, txn, base_currency, fx_mode, allow_fetch=(fx_mode == "frankfurter"))
    if category_id is None:
        # Only guess when the user didn't choose: auto_categorise ranks manual
        # above every automatic source, but passing an already-set row through it
        # is pointless work.
        import_service.auto_categorise(db, txn)
    db.commit()
    db.refresh(txn)
    return txn
