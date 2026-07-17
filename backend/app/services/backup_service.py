"""Backup & restore (spec §26.5; backlog #9, #10).

Two flavours:
- Full **database** backup/restore — a consistent SQLite snapshot you can
  download, and a validated restore that swaps the file back in.
- **Config** export/import — settings + category/vendor library as portable
  JSON (safer to share, survives schema changes).

Encrypted / cloud backup (backlog #15) is deliberately not here yet — it needs
a decision on master-key management. See docs/security.md.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import session as dbsession
from app.logging import get_logger
from app.models import Category, Setting, Vendor, VendorAlias
from app.services import settings_service

logger = get_logger(__name__)

SQLITE_MAGIC = b"SQLite format 3\x00"
# Tables we expect in a genuine HA Finance database (sanity check on restore).
_REQUIRED_TABLES = {"transactions", "categories", "statements", "accounts"}


class RestoreError(Exception):
    """Raised when an uploaded database fails validation."""


# --- Full database backup/restore ---

def snapshot_database() -> Path:
    """Return a path to a consistent point-in-time copy of the database.

    Uses SQLite's online backup API so the copy is valid even with WAL writes
    in flight. Caller is responsible for deleting the temp file.
    """
    src = settings.database_file
    fd, tmp_name = tempfile.mkstemp(prefix="hafi-backup-", suffix=".db")
    os.close(fd)
    tmp = Path(tmp_name)
    # NB: `with sqlite3.connect(...)` commits but does NOT close the connection,
    # which would leave the file locked on Windows. Close explicitly.
    src_con = sqlite3.connect(str(src))
    dst_con = sqlite3.connect(str(tmp))
    try:
        src_con.backup(dst_con)
    finally:
        dst_con.close()
        src_con.close()
    return tmp


def restore_database(content: bytes) -> None:
    """Validate an uploaded SQLite file and swap it in as the live database.

    The current database is copied to ``<db>.bak`` first. Destructive — the UI
    confirms before calling this.
    """
    if not content.startswith(SQLITE_MAGIC):
        raise RestoreError("Uploaded file is not a SQLite database.")

    fd, tmp_name = tempfile.mkstemp(prefix="hafi-restore-", suffix=".db")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_bytes(content)
        con = sqlite3.connect(str(tmp))
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {
                row[0]
                for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            con.close()
        if integrity != "ok":
            raise RestoreError(f"Database failed integrity check: {integrity}")
        missing = _REQUIRED_TABLES - tables
        if missing:
            raise RestoreError(
                "This does not look like a HA Finance database "
                f"(missing tables: {', '.join(sorted(missing))})."
            )

        # Release SQLAlchemy's pooled connections before replacing the file.
        engine = dbsession.get_engine()
        if engine is not None:
            engine.dispose()
        dest = settings.database_file
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.copy2(dest, dest.with_name(dest.name + ".bak"))
        shutil.copyfile(tmp, dest)
        # Drop stale WAL/SHM so the restored file is authoritative.
        for suffix in ("-wal", "-shm"):
            Path(str(dest) + suffix).unlink(missing_ok=True)
        logger.info("Database restored from upload (%s bytes)", len(content))
    finally:
        tmp.unlink(missing_ok=True)


# --- Safety backups + trim (backlog #78) ---
#
# The retention engine takes a timestamped snapshot before any purge so a botched
# cleanup is recoverable. These live in a dedicated ``backups/`` dir beside the
# private database and are trimmed by age/size so they can't grow without bound.

def backups_dir() -> Path:
    d = settings.database_file.parent / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_safety_backup(label: str) -> Path:
    """Snapshot the live DB into ``backups/<label>-<timestamp>.db`` and return it.

    Uses microsecond precision so two backups in the same second don't collide.
    """
    snap = snapshot_database()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    dest = backups_dir() / f"{label}-{stamp}.db"
    shutil.move(str(snap), str(dest))
    logger.info("Wrote safety backup %s (%d bytes)", dest.name, dest.stat().st_size)
    return dest


def prune_backups(db: Session) -> dict:
    """Trim the safety-backup history to the configured age/size limits, but never
    drop below ``min_keep`` most-recent files (so there's always a safety net)."""
    policy = settings_service.get_backup_trim_policy(db)
    files = sorted(backups_dir().glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    protected = files[: policy["min_keep"]]
    deletable = files[policy["min_keep"]:]  # oldest beyond the keep-floor (still newest-first)

    removed = 0
    age_cutoff = time.time() - policy["max_age_days"] * 86400
    survivors: list[Path] = []
    for p in deletable:
        if p.stat().st_mtime < age_cutoff:
            p.unlink(missing_ok=True)
            removed += 1
        else:
            survivors.append(p)

    max_bytes = policy["max_total_mb"] * 1024 * 1024

    # Stat every surviving file once, then track the running total as we delete
    # so the size-cap loop stays O(n) instead of re-scanning on each iteration.
    sizes = {p: p.stat().st_size for p in [*protected, *survivors] if p.exists()}
    running_total = sum(sizes.values())

    # survivors is newest-first; pop the oldest until under the size cap.
    while survivors and running_total > max_bytes:
        victim = survivors.pop()
        running_total -= sizes.get(victim, 0)
        victim.unlink(missing_ok=True)
        removed += 1

    kept = len([p for p in files if p.exists()])
    if removed:
        logger.info("Pruned %d old safety backup(s); %d kept.", removed, kept)
    return {"removed": removed, "kept": kept}


# --- Config / library export-import (portable JSON) ---

def export_config(db: Session) -> dict:
    """Export settings + category/vendor library as a portable JSON document."""
    categories = db.scalars(select(Category)).all()
    vendors = db.scalars(select(Vendor)).all()
    aliases = db.scalars(select(VendorAlias)).all()
    aliases_by_vendor: dict[int, list[dict]] = {}
    for a in aliases:
        aliases_by_vendor.setdefault(a.vendor_id, []).append(
            {"alias": a.alias, "match_type": a.match_type, "source": a.source}
        )
    return {
        "version": "0.1",
        "settings": [{"key": s.key, "value": s.value} for s in db.scalars(select(Setting)).all()],
        "categories": [
            {
                "library_id": c.library_id,
                "name": c.name,
                "icon": c.icon,
                "colour": c.colour,
                "privacy_sensitivity": c.privacy_sensitivity,
                "is_budgetable": c.is_budgetable,
                "is_system": c.is_system,
            }
            for c in categories
        ],
        "vendors": [
            {
                "canonical_name": v.canonical_name,
                "display_name": v.display_name,
                "service_type": v.service_type,
                "website": v.website,
                "notes": v.notes,
                "aliases": aliases_by_vendor.get(v.id, []),
            }
            for v in vendors
        ],
    }


def _import_categories(db: Session, data: dict, household_id: int) -> int:
    """Upsert categories by name; return the number newly added."""
    added = 0
    existing_cats = {c.name: c for c in db.scalars(select(Category)).all()}
    for entry in data.get("categories", []):
        if entry["name"] in existing_cats:
            continue
        db.add(
            Category(
                household_id=household_id,
                name=entry["name"],
                path=entry["name"],
                library_id=entry.get("library_id"),
                icon=entry.get("icon"),
                colour=entry.get("colour"),
                privacy_sensitivity=entry.get("privacy_sensitivity", "normal"),
                is_budgetable=entry.get("is_budgetable", True),
                is_system=entry.get("is_system", False),
            )
        )
        added += 1
    return added


def _import_vendors(db: Session, data: dict, household_id: int) -> int:
    """Upsert vendors (and their aliases) by canonical name; return count added."""
    added = 0
    existing_vendors = {v.canonical_name: v for v in db.scalars(select(Vendor)).all()}
    for entry in data.get("vendors", []):
        if entry["canonical_name"] in existing_vendors:
            continue
        vendor = Vendor(
            household_id=household_id,
            canonical_name=entry["canonical_name"],
            display_name=entry.get("display_name"),
            service_type=entry.get("service_type"),
            website=entry.get("website"),
            notes=entry.get("notes"),
            created_by="import",
        )
        db.add(vendor)
        db.flush()
        for alias in entry.get("aliases", []):
            db.add(
                VendorAlias(
                    vendor_id=vendor.id,
                    alias=alias["alias"],
                    match_type=alias.get("match_type", "contains"),
                    source="import",
                )
            )
        added += 1
    return added


def import_config(db: Session, data: dict) -> dict:
    """Merge a config export back in (non-destructive upsert by name/key).

    The whole merge runs as a single transaction: if any step fails the session
    is rolled back so the settings/library tables are never left partially
    written, and the original error is re-raised for the caller to surface.
    """
    from app.services.household_service import get_or_create_default_household

    household = get_or_create_default_household(db)

    try:
        cats_added = _import_categories(db, data, household.id)
        vendors_added = _import_vendors(db, data, household.id)
        # Settings: only allowlisted, validated keys are applied (CR-SEC-2). An
        # import must not be able to flip privacy_mode, set an internal AI/Paperless
        # URL, or write arbitrary keys — see settings_service.IMPORTABLE_SETTINGS.
        settings_result = settings_service.apply_imported_settings(db, data.get("settings", []))
        db.commit()
    except Exception:
        db.rollback()
        raise

    return {
        "categories_added": cats_added,
        "vendors_added": vendors_added,
        "settings_set": settings_result["settings_set"],
        "settings_skipped": settings_result["settings_skipped"],
        "skipped_setting_keys": settings_result["skipped_setting_keys"],
    }
