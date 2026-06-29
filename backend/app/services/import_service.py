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
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

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
    curve_link_service,
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


def _build_preview(
    parsed: list[StandardTransaction],
    account_id: int,
    existing_hashes: set[str],
    cross: dict[int, curve_link_service.CrossMatch],
    preview_limit: int,
) -> tuple[int, int, list[dict]]:
    """Walk the parsed rows: tally new vs duplicate (same-account hash or a
    high-confidence Curve cross-match) and build the capped preview list."""
    new_count = 0
    dup_count = 0
    seen: set[str] = set()
    preview: list[dict] = []
    for idx, txn in enumerate(parsed):
        h = source_hash(account_id, txn)
        same_dup = h in existing_hashes or h in seen
        seen.add(h)
        match = cross.get(idx)
        cross_skip = match is not None and match.confidence == "high"
        is_dup = same_dup or cross_skip
        if is_dup:
            dup_count += 1
        else:
            new_count += 1
        dup_reason = match.reason if cross_skip else None
        warning = match.reason if (match is not None and not cross_skip and not same_dup) else None
        if len(preview) < preview_limit:
            preview.append(_preview_row(txn, is_dup, dup_reason=dup_reason, warning=warning))
    return new_count, dup_count, preview


def create_import(
    db: Session,
    filename: str,
    content: bytes,
    parser_id: str | None = None,
    account_id: int | None = None,
    mapping: dict | None = None,
    preview_limit: int = 20,
    parser: Any = None,
) -> dict:
    """Parse + dedupe a file and create a pending Statement. Returns the upload
    response (spec §24.3) with a preview and report. ``parser`` may be supplied
    pre-built (e.g. the AI image-extract path injects already-parsed rows);
    otherwise it's resolved from ``parser_id``/detection."""
    if parser is None:
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

    # Cross-account dedup for Curve (overlay card): the same spend also lands on
    # the underlying funding card's own statement (curve_link_service). A
    # Curve-marked match is skipped like a duplicate; an unmarked amount+date
    # match is surfaced as a *possible* duplicate but kept.
    links = curve_link_service.link_map(db)
    cross = curve_link_service.detect_cross_account(
        db, target_account_id=account.id, parsed_rows=parsed, links=links
    )

    new_count, dup_count, preview = _build_preview(
        parsed, account.id, existing_hashes, cross, preview_limit
    )

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
    notes: dict = {
        "parser_id": parser.parser_id,
        "mapping": mapping,
        "account_id": account.id,
        "stored_path": str(stored_path),
    }
    # AI image-extract rows can't be re-parsed from the stored image on confirm,
    # so persist the already-parsed rows to rebuild them there (fixes the
    # "Unknown parser: ai_image_extract" failure on confirm).
    if isinstance(parser, _RowsParser):
        notes["ai_rows"] = _serialize_rows(parsed)
    statement.notes = json.dumps(notes)
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
        "funding_labels": curve_link_service.funding_labels_for_rows(db, parsed),
    }


# Pseudo-parser id for AI image-extracted imports. The stored file is the raw
# image (not re-parseable), so confirm rebuilds the rows from ``notes["ai_rows"]``
# instead of re-running a parser keyed by this id.
AI_ROWS_PARSER_ID = "ai_image_extract"


class _RowsParser:
    """A pseudo-parser that yields already-parsed rows, so AI image-extraction can
    reuse the normal create_import pipeline (dedupe, Statement, preview, confirm)."""

    def __init__(self, rows: list[StandardTransaction], *, institution: str, fmt: str):
        self._rows = rows
        self.parser_id = AI_ROWS_PARSER_ID
        self.institution = institution
        self.format = fmt

    def parse(self, _filename: str, _content: bytes) -> list[StandardTransaction]:
        return self._rows  # rows are already parsed; the parser interface requires these args


def _serialize_rows(rows: list[StandardTransaction]) -> list[dict]:
    """Persist already-parsed rows on the pending Statement so an AI image-extract
    import can be re-materialised on confirm (the stored image can't be re-parsed)."""
    return [
        {
            "transaction_date": r.transaction_date.isoformat(),
            "posted_date": r.posted_date.isoformat() if r.posted_date else None,
            "amount": str(r.amount),
            "currency": r.currency,
            "description_raw": r.description_raw,
            "merchant_raw": r.merchant_raw,
            "external_id": r.external_id,
            "needs_review": r.needs_review,
        }
        for r in rows
    ]


def _deserialize_rows(raw: list[dict]) -> list[StandardTransaction]:
    """Inverse of :func:`_serialize_rows` — rebuild the staged rows on confirm."""
    return [
        StandardTransaction(
            transaction_date=date.fromisoformat(r["transaction_date"]),
            amount=Decimal(str(r["amount"])),
            currency=r["currency"],
            description_raw=r["description_raw"],
            posted_date=date.fromisoformat(r["posted_date"]) if r.get("posted_date") else None,
            merchant_raw=r.get("merchant_raw"),
            external_id=r.get("external_id"),
            needs_review=bool(r.get("needs_review", True)),
        )
        for r in raw
    ]


def create_import_from_rows(
    db: Session, filename: str, content: bytes, rows: list[StandardTransaction],
    *, account_id: int | None = None, institution: str = "AI-extracted", fmt: str = "image",
) -> dict:
    """Create an import from pre-parsed rows (the AI image-extract path) — same
    dedupe/preview/confirm flow as a normal upload."""
    return create_import(
        db, filename, content, account_id=account_id,
        parser=_RowsParser(rows, institution=institution, fmt=fmt),
    )


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
    review_reason: str | None = None,
) -> tuple[int, int, int]:
    """Persist one parsed transaction, skipping exact duplicates.

    Returns ``(new, categorised, needs_rate)`` deltas (each 0 or 1) and mutates
    ``existing_hashes`` so duplicates within the same statement are caught.
    ``review_reason`` flags a kept row for review (e.g. a possible cross-account
    Curve duplicate that wasn't auto-skipped)."""
    h = source_hash(account_id, txn)
    if h in existing_hashes:
        return 0, 0, 0  # duplicate
    existing_hashes.add(h)
    row = _to_transaction(txn, household_id, account_id, statement_id, h)
    if review_reason and not row.needs_review:
        row.needs_review = True
        row.review_reason = review_reason
    db.add(row)
    db.flush()
    categorised = 1 if auto_categorise(db, row) else 0
    # A parser may force a specific library category (e.g. earned Curve Cash →
    # Cashback), which must win over the keyword guess on its merchant text.
    if txn.category_library_id:
        forced = category_service.resolve_library_category(db, txn.category_library_id)
        if forced is not None:
            row.category_id = forced
            categorised = 1
    # Convert to base currency (backlog #29). In manual mode, foreign rows
    # with no cached rate are flagged needs_rate for later backfill.
    needs_rate = 0 if fx_service.convert_transaction(
        db, row, base_currency, fx_mode, allow_fetch=(fx_mode == "frankfurter")
    ) else 1
    return 1, categorised, needs_rate


def _statement_config(statement: Statement) -> dict:
    """Parse the JSON config stashed in ``statement.notes`` (parser id, stored
    path, staged AI rows). Tolerates non-JSON / legacy free-text notes → {} so
    confirm/delete never crash on a malformed value (SR-A1)."""
    try:
        config = json.loads(statement.notes or "{}")
    except (ValueError, TypeError):
        return {}
    return config if isinstance(config, dict) else {}


def _load_parsed_rows(db: Session, statement: Statement, config: dict) -> list[StandardTransaction]:
    """Rebuild the rows to persist on confirm: AI image-extract rows were staged at
    upload (the image can't be re-parsed); everything else is re-parsed from the
    stored file. Marks the statement failed + raises ImportFailed on a parse error."""
    ai_rows = config.get("ai_rows")
    if ai_rows is not None:
        return _deserialize_rows(ai_rows)
    parser = _resolve_parser(config.get("parser_id"), statement.source_filename or "", b"", config.get("mapping"))
    stored_path = Path(config["stored_path"])
    if not stored_path.is_file():
        raise ImportFailed("Uploaded file is no longer available; please re-upload")
    content = stored_path.read_bytes()
    try:
        return parser.parse(statement.source_filename or "", content)
    except ParseError as exc:
        statement.status = "failed"
        db.commit()
        raise ImportFailed(f"Parse failed on confirm: {exc}") from exc


def _persist_rows(
    db: Session,
    parsed: list[StandardTransaction],
    cross: dict[int, curve_link_service.CrossMatch],
    *,
    household_id: int,
    account_id: int,
    statement_id: int,
    existing_hashes: set[str],
    base_currency: str,
    fx_mode: str,
) -> tuple[int, int, int, int]:
    """Persist each parsed row (skipping exact + high-confidence Curve duplicates).
    Returns (new, duplicates, auto-categorised, needs-rate) counts."""
    new_count = dup_count = categorised = needs_rate = 0
    for idx, txn in enumerate(parsed):
        match = cross.get(idx)
        if match is not None and match.confidence == "high":
            dup_count += 1  # also on the linked funding account → skip
            continue
        flag = "possible_duplicate" if (match is not None and match.confidence == "low") else None
        new_delta, cat_delta, rate_delta = _persist_parsed_transaction(
            db, txn,
            household_id=household_id,
            account_id=account_id,
            statement_id=statement_id,
            existing_hashes=existing_hashes,
            base_currency=base_currency,
            fx_mode=fx_mode,
            review_reason=flag,
        )
        if not new_delta:
            dup_count += 1
            continue
        new_count += new_delta
        categorised += cat_delta
        needs_rate += rate_delta
    return new_count, dup_count, categorised, needs_rate


def confirm_import(db: Session, import_id: int) -> dict:
    """Persist the transactions for a pending statement, skipping exact
    duplicates (spec §14.5)."""
    statement = db.get(Statement, import_id)
    if statement is None:
        raise ImportFailed(f"Import {import_id} not found")
    if statement.status == "imported":
        raise ImportFailed(f"Import {import_id} was already confirmed")

    parsed = _load_parsed_rows(db, statement, _statement_config(statement))

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

    # Same cross-account Curve dedup as the preview (curve_link_service): skip a
    # Curve-marked match, keep-but-flag an unmarked possible match.
    links = curve_link_service.link_map(db)
    cross = curve_link_service.detect_cross_account(
        db, target_account_id=account.id, parsed_rows=parsed, links=links
    )

    new_count, dup_count, categorised, needs_rate = _persist_rows(
        db, parsed, cross,
        household_id=household.id,
        account_id=account.id,
        statement_id=statement.id,
        existing_hashes=existing_hashes,
        base_currency=base_currency,
        fx_mode=fx_mode,
    )

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
    config = _statement_config(statement)
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
        funding_source=txn.funding_source,
        is_income=txn.is_income,
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
        auto_categorise(db, txn)
        if not had_category and txn.category_id is not None:
            count += 1
    db.commit()
    return count


def auto_categorise(db: Session, txn: Transaction) -> bool:
    """Categorisation order (spec §15.1): manual > rule > vendor default >
    keyword. Returns True if the transaction ends up with a category.

    Public so other services (e.g. a transaction materialised from a receipt)
    can categorise a freshly-created row the same way an import does."""
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


def _preview_row(
    txn: StandardTransaction,
    is_duplicate: bool,
    *,
    dup_reason: str | None = None,
    warning: str | None = None,
) -> dict:
    return {
        "transaction_date": txn.transaction_date.isoformat(),
        "description_raw": txn.description_raw,
        "merchant_raw": txn.merchant_raw,
        "amount": f"{txn.amount:.2f}",
        "currency": txn.currency,
        "direction": txn.direction,
        "category_hint": txn.category_hint,
        "is_duplicate": is_duplicate,
        # Set when the duplicate is a cross-account Curve match (vs a plain
        # same-account dupe); `warning` marks a kept-but-possible cross match.
        "dup_reason": dup_reason,
        "warning": warning,
    }
