"""CSV import service (spec §14).

Workflow: upload -> detect parser -> parse -> normalise -> dedupe -> preview,
then confirm -> persist statement + transactions (skipping exact duplicates).

Uploaded files are stored under ``<db dir>/uploads`` and the parser config is
recorded on the pending Statement (in ``notes`` as JSON) so confirm can
re-parse the exact same file.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.logging import get_logger
from app.models import Account, Statement, Transaction
from app.parsers import StandardTransaction, detect_parser, get_parser
from app.parsers.base import ParseError
from app.parsers.generic_csv import GenericCsvParser
from app.services import (
    category_service,
    fx_service,
    rule_service,
    settings_service,
    vendor_service,
)
from app.services.household_service import (
    get_or_create_account,
    get_or_create_default_household,
)

logger = get_logger(__name__)


class ImportFailed(Exception):
    """Raised for import problems that should surface as a 4xx to the client."""


@dataclass
class ImportReport:
    rows_detected: int
    new_count: int
    duplicate_count: int
    error_count: int

    def as_dict(self) -> dict:
        return {
            "rows_detected": self.rows_detected,
            "new": self.new_count,
            "duplicates": self.duplicate_count,
            "errors": self.error_count,
        }


def _uploads_dir() -> Path:
    path = settings.database_file.parent / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def source_hash(account_id: int | None, txn: StandardTransaction) -> str:
    """Per-transaction dedup key (spec §14.5):
    sha256(account|date|amount|currency|description_raw|posted_date)."""
    parts = [
        str(account_id or ""),
        txn.transaction_date.isoformat(),
        f"{txn.amount:.2f}",
        txn.currency,
        txn.description_raw,
        txn.posted_date.isoformat() if txn.posted_date else "",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


# --- Bootstrap helpers (single-user MVP still needs a household + account) ---

def _resolve_parser(parser_id: str | None, filename: str, content: bytes, mapping: dict | None):
    if parser_id:
        if parser_id == "generic_csv":
            return GenericCsvParser(mapping=mapping, default_currency=settings.currency)
        parser = get_parser(parser_id)
        if parser is None:
            raise ImportFailed(f"Unknown parser: {parser_id}")
        return parser
    parser = detect_parser(filename, content)
    if isinstance(parser, GenericCsvParser) and mapping:
        return GenericCsvParser(mapping=mapping, default_currency=settings.currency)
    return parser


def create_import(
    db: Session,
    filename: str,
    content: bytes,
    parser_id: str | None = None,
    account_id: int | None = None,
    mapping: dict | None = None,
    preview_limit: int = 20,
) -> dict:
    """Parse + dedupe a file and create a pending Statement. Returns the upload
    response (spec §24.3) with a preview and report."""
    parser = _resolve_parser(parser_id, filename, content, mapping)

    try:
        parsed = parser.parse(filename, content)
    except ParseError as exc:
        raise ImportFailed(f"Could not parse file with {parser.parser_id}: {exc}") from exc

    household = get_or_create_default_household(db)
    try:
        account = get_or_create_account(db, household, parser.institution, account_id)
    except ValueError as exc:
        raise ImportFailed(str(exc)) from exc

    fhash = file_hash(content)
    warnings: list[str] = []
    already = db.scalars(
        select(Statement).where(
            Statement.source_hash == fhash, Statement.status == "imported"
        )
    ).first()
    if already:
        warnings.append(f"This file was already imported (statement #{already.id}).")

    # Existing per-transaction hashes for this account.
    existing_hashes = {
        h
        for h in db.scalars(
            select(Transaction.source_hash).where(Transaction.account_id == account.id)
        ).all()
        if h is not None
    }

    new_count = 0
    dup_count = 0
    seen: set[str] = set()
    preview: list[dict] = []
    for txn in parsed:
        h = source_hash(account.id, txn)
        is_dup = h in existing_hashes or h in seen
        if is_dup:
            dup_count += 1
        else:
            new_count += 1
            seen.add(h)
        if len(preview) < preview_limit:
            preview.append(_preview_row(txn, is_dup))

    statement = Statement(
        account_id=account.id,
        source_type="manual_upload",
        source_format=parser.format,
        source_filename=filename,
        source_hash=fhash,
        status="pending",
        transaction_count=0,
        duplicate_count=0,
    )
    db.add(statement)
    db.flush()

    stored_path = _uploads_dir() / f"{statement.id}.{parser.format}"
    stored_path.write_bytes(content)
    statement.notes = json.dumps(
        {
            "parser_id": parser.parser_id,
            "mapping": mapping,
            "account_id": account.id,
            "stored_path": str(stored_path),
        }
    )
    db.commit()

    report = ImportReport(len(parsed), new_count, dup_count, 0)
    return {
        "import_id": statement.id,
        "detected_parser": parser.parser_id,
        "institution": parser.institution,
        "account_id": account.id,
        "rows_detected": len(parsed),
        "report": report.as_dict(),
        "preview": preview,
        "warnings": warnings,
    }


def _persist_parsed_transaction(
    db: Session,
    txn: StandardTransaction,
    *,
    household_id: int,
    account_id: int,
    statement_id: int,
    existing_hashes: set[str],
    base_currency: str,
    fx_mode: str,
) -> tuple[int, int, int]:
    """Persist one parsed transaction, skipping exact duplicates.

    Returns ``(new, categorised, needs_rate)`` deltas (each 0 or 1) and mutates
    ``existing_hashes`` so duplicates within the same statement are caught."""
    h = source_hash(account_id, txn)
    if h in existing_hashes:
        return 0, 0, 0  # duplicate
    existing_hashes.add(h)
    row = _to_transaction(txn, household_id, account_id, statement_id, h)
    db.add(row)
    db.flush()
    categorised = 1 if _auto_categorise(db, row) else 0
    # Convert to base currency (backlog #29). In manual mode, foreign rows
    # with no cached rate are flagged needs_rate for later backfill.
    needs_rate = 0 if fx_service.convert_transaction(
        db, row, base_currency, fx_mode, allow_fetch=(fx_mode == "frankfurter")
    ) else 1
    return 1, categorised, needs_rate


def confirm_import(db: Session, import_id: int) -> dict:
    """Persist the transactions for a pending statement, skipping exact
    duplicates (spec §14.5)."""
    statement = db.get(Statement, import_id)
    if statement is None:
        raise ImportFailed(f"Import {import_id} not found")
    if statement.status == "imported":
        raise ImportFailed(f"Import {import_id} was already confirmed")

    config = json.loads(statement.notes or "{}")
    parser = _resolve_parser(config.get("parser_id"), statement.source_filename or "", b"", config.get("mapping"))
    stored_path = Path(config["stored_path"])
    if not stored_path.is_file():
        raise ImportFailed("Uploaded file is no longer available; please re-upload")
    content = stored_path.read_bytes()

    try:
        parsed = parser.parse(statement.source_filename or "", content)
    except ParseError as exc:
        statement.status = "failed"
        db.commit()
        raise ImportFailed(f"Parse failed on confirm: {exc}") from exc

    account = db.get(Account, statement.account_id)
    if account is None:
        raise ImportFailed(f"Account {statement.account_id} for import {import_id} no longer exists")
    household = get_or_create_default_household(db)
    # Categories must exist for keyword/vendor categorisation (spec §15.1).
    category_service.ensure_default_categories(db)
    base_currency = settings_service.get_base_currency(db)
    fx_mode = settings_service.get_fx_mode(db)

    existing_hashes = {
        h
        for h in db.scalars(
            select(Transaction.source_hash).where(Transaction.account_id == account.id)
        ).all()
        if h is not None
    }

    new_count = 0
    dup_count = 0
    categorised = 0
    needs_rate = 0
    for txn in parsed:
        new_delta, cat_delta, rate_delta = _persist_parsed_transaction(
            db,
            txn,
            household_id=household.id,
            account_id=account.id,
            statement_id=statement.id,
            existing_hashes=existing_hashes,
            base_currency=base_currency,
            fx_mode=fx_mode,
        )
        if not new_delta:
            dup_count += 1
            continue
        new_count += new_delta
        categorised += cat_delta
        needs_rate += rate_delta

    statement.status = "imported"
    statement.transaction_count = new_count
    statement.duplicate_count = dup_count
    statement.imported_at = datetime.now(UTC)
    if parsed:
        dates = [t.transaction_date for t in parsed]
        statement.period_start = min(dates)
        statement.period_end = max(dates)
    db.commit()

    # Detect recurring payments / subscriptions from the new data (spec §20.1),
    # then refresh Home Assistant sensors (spec §27.1). Both best-effort: never
    # let them fail a completed import.
    from app.services import mqtt_service, subscription_service

    try:
        subscription_service.detect(db)
    except Exception:  # pragma: no cover - detection must never break an import
        logger.warning("Subscription detection failed (non-fatal)", exc_info=True)
    mqtt_service.publish_safe(db)

    logger.info(
        "Import %s confirmed: %s new, %s duplicates, %s auto-categorised, %s need FX rate",
        import_id,
        new_count,
        dup_count,
        categorised,
        needs_rate,
    )
    return {
        "import_id": statement.id,
        "status": statement.status,
        "report": ImportReport(len(parsed), new_count, dup_count, 0).as_dict(),
    }


def delete_import(db: Session, import_id: int) -> None:
    statement = db.get(Statement, import_id)
    if statement is None:
        raise ImportFailed(f"Import {import_id} not found")
    config = json.loads(statement.notes or "{}")
    stored = config.get("stored_path")
    if stored:
        Path(stored).unlink(missing_ok=True)
    # Deleting an imported statement detaches its transactions (FK SET NULL).
    db.delete(statement)
    db.commit()


def _to_transaction(
    txn: StandardTransaction, household_id: int, account_id: int, statement_id: int, h: str
) -> Transaction:
    return Transaction(
        household_id=household_id,
        account_id=account_id,
        statement_id=statement_id,
        external_id=txn.external_id,
        transaction_date=txn.transaction_date,
        posted_date=txn.posted_date,
        description_raw=txn.description_raw,
        merchant_raw=txn.merchant_raw,
        amount=txn.amount,
        currency=txn.currency,
        direction=txn.direction,
        source_hash=h,
        needs_review=txn.needs_review,
        review_reason="pdf_unverified" if txn.needs_review else None,
    )


def recategorise(db: Session, only_uncategorised: bool = True) -> int:
    """Re-run vendor + keyword categorisation across stored transactions.

    Never overwrites an existing category (auto-categorise only fills blanks),
    so manual choices are preserved. Returns the number newly categorised.
    """
    category_service.ensure_default_categories(db)
    stmt = select(Transaction)
    if only_uncategorised:
        stmt = stmt.where(Transaction.category_id.is_(None))
    count = 0
    for txn in db.scalars(stmt).all():
        had_category = txn.category_id is not None
        _auto_categorise(db, txn)
        if not had_category and txn.category_id is not None:
            count += 1
    db.commit()
    return count


def _auto_categorise(db: Session, txn: Transaction) -> bool:
    """Categorisation order (spec §15.1): manual > rule > vendor default >
    keyword. Returns True if the transaction ends up with a category."""
    # 1. Rules (highest precedence after manual; may also set vendor/flags).
    rule_service.apply_rules(db, txn)
    # 2. Vendor alias match (sets merchant; category only if still unset).
    vendor_service.normalise_transaction(db, txn)
    if txn.category_id is not None:
        return True
    # 3. Category-library keyword fallback.
    cat_id, conf = category_service.categorise_text(db, txn.description_raw)
    if cat_id is not None:
        txn.category_id = cat_id
        txn.confidence_score = conf
        return True
    return False


def _preview_row(txn: StandardTransaction, is_duplicate: bool) -> dict:
    return {
        "transaction_date": txn.transaction_date.isoformat(),
        "description_raw": txn.description_raw,
        "merchant_raw": txn.merchant_raw,
        "amount": f"{txn.amount:.2f}",
        "currency": txn.currency,
        "direction": txn.direction,
        "category_hint": txn.category_hint,
        "is_duplicate": is_duplicate,
    }
