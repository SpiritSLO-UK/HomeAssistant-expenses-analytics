"""Schemas for the import API (spec §24.3)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

# How a saved profile reads ambiguous CSV dates (maps to GenericCsvParser):
# "auto" = per-file heuristic (default), "dmy" = UK day-first, "mdy" = US month-first.
DateFormat = Literal["auto", "dmy", "mdy"]


class ImportReportSchema(BaseModel):
    rows_detected: int
    new: int
    duplicates: int
    errors: int


class PreviewRow(BaseModel):
    transaction_date: str
    description_raw: str
    merchant_raw: str | None = None
    amount: str
    currency: str
    direction: str
    category_hint: str | None = None
    is_duplicate: bool
    # Why this row is a duplicate, when it's a cross-account Curve match (vs a
    # plain same-account dupe). `warning` flags a kept-but-possible cross match.
    dup_reason: str | None = None
    warning: str | None = None


class FundingLabel(BaseModel):
    """A Curve funding-card label found in an upload + its current mapping."""

    label: str
    count: int
    account_id: int | None = None
    account_name: str | None = None


class UploadResponse(BaseModel):
    import_id: int
    detected_parser: str
    institution: str
    account_id: int
    rows_detected: int
    report: ImportReportSchema
    preview: list[PreviewRow]
    warnings: list[str]
    # Distinct Curve funding-card labels in this upload (empty for ordinary
    # statements) — drives the Import page's "map this card to an account" panel.
    funding_labels: list[FundingLabel] = []


class FundingLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    label: str
    account_id: int


class FundingLinkUpdate(BaseModel):
    label: str
    # None clears the mapping.
    account_id: int | None = None


class ConfirmResponse(BaseModel):
    import_id: int
    status: str
    report: ImportReportSchema


class ImportListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int | None
    source_filename: str | None
    source_format: str | None
    status: str
    transaction_count: int
    duplicate_count: int
    period_start: date | None = None
    period_end: date | None = None
    imported_at: datetime | None = None


class ParserInfo(BaseModel):
    parser_id: str
    institution: str


# --- Custom CSV column mapping + saved import profiles ---


class InspectField(BaseModel):
    """A logical field the user can map a CSV column to."""

    key: str
    label: str
    required: bool = False


class InspectResponse(BaseModel):
    """Detected columns + a heuristic mapping, to drive the column-mapping UI."""

    headers: list[str]
    sample_rows: list[dict[str, str]]
    suggested_mapping: dict[str, str]
    fields: list[InspectField]


class ImportProfileIn(BaseModel):
    name: str
    mapping: dict[str, str]
    default_currency: str = "GBP"
    # Pin the CSV date order for this bank's statements (US month-first / UK
    # day-first); "auto" keeps the historic per-file heuristic.
    date_format: DateFormat = "auto"


class ImportProfileOut(BaseModel):
    id: int
    name: str
    mapping: dict[str, str]
    default_currency: str
    date_format: DateFormat = "auto"
