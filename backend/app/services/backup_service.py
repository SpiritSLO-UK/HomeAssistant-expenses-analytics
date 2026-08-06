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
from app.models import Category, Project, Rule, Setting, Vendor, VendorAlias
from app.services import settings_service

logger = get_logger(__name__)

SQLITE_MAGIC = b"SQLite format 3\x00"
# Tables we expect in a genuine HA Finance database (sanity check on restore).
_REQUIRED_TABLES = {"transactions", "categories", "statements", "accounts"}


class RestoreError(Exception):
    """Raised when an uploaded database fails validation."""


def _encryption_enabled() -> bool:
    """True when the at-rest-encryption marker reports the live DB is encrypted."""
    from app.services import security_service

    marker = security_service.read_marker()
    return bool(marker and marker.get("enabled"))


# --- Full database backup/restore ---

def _snapshot_plaintext(src: Path, dst: Path) -> None:
    """Copy a plaintext SQLite DB via the online backup API (WAL-safe)."""
    # NB: `with sqlite3.connect(...)` commits but does NOT close the connection,
    # which would leave the file locked on Windows. Close explicitly.
    src_con = sqlite3.connect(str(src))
    dst_con = sqlite3.connect(str(dst))
    try:
        # Wait for a concurrent writer to release its lock instead of failing
        # immediately with "database is locked" mid-backup.
        src_con.execute("PRAGMA busy_timeout=5000")
        src_con.backup(dst_con)
    finally:
        dst_con.close()
        src_con.close()


def _snapshot_encrypted(dst: Path) -> None:
    """Write a PLAINTEXT snapshot of the encrypted live DB to ``dst``.

    stdlib ``sqlite3`` can't decrypt a SQLCipher file, and in prompt-unlock mode the
    passphrase is not stored anywhere readable — it only lives inside the active
    engine's connection ``creator``. So borrow a keyed connection from the engine
    pool and run ``sqlcipher_export`` into an unkeyed (``KEY ''``) attached database,
    mirroring :func:`security_service.disable_encryption`. The result is an ordinary
    SQLite file the existing plaintext restore/validate path and the AES-wrapping
    encrypted-download accept unchanged.
    """
    from app.services import security_service

    raw = dbsession.require_engine().raw_connection()
    try:
        con = raw.driver_connection
        con.execute("PRAGMA busy_timeout=5000")
        literal = security_service._sql_string_literal(str(dst))
        con.execute(f"ATTACH DATABASE {literal} AS plaintext KEY ''")
        con.execute("SELECT sqlcipher_export('plaintext')")
        con.execute("DETACH DATABASE plaintext")
    finally:
        raw.close()  # returns the connection to the pool; does not dispose the engine


def snapshot_database() -> Path:
    """Return a path to a consistent point-in-time plaintext copy of the database.

    On a plaintext install this uses SQLite's online backup API so the copy is valid
    even with WAL writes in flight. On an at-rest-encrypted (SQLCipher) install it
    decrypts into a plaintext snapshot via the active keyed connection, so downloads,
    the encrypted-backup wrapper, and the retention safety-backup all work rather than
    failing with "file is not a database". Caller deletes the temp file.
    """
    fd, tmp_name = tempfile.mkstemp(prefix="hafi-backup-", suffix=".db")
    os.close(fd)
    tmp = Path(tmp_name)
    if _encryption_enabled():
        _snapshot_encrypted(tmp)
    else:
        _snapshot_plaintext(settings.database_file, tmp)
    return tmp


def _validate_restore_candidate(path: Path) -> None:
    """Raise ``RestoreError`` unless ``path`` is a sound HA Finance database."""
    con = sqlite3.connect(str(path))
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


def _quiesce_database(dest: Path) -> None:
    """Best-effort quiesce of the live DB before we overwrite it: wait (honouring
    a busy_timeout) for any in-flight writer, then checkpoint the WAL into the
    main file so the copy we're about to replace is self-contained."""
    if not dest.exists():
        return
    if _encryption_enabled():
        # The live file is SQLCipher-encrypted: stdlib sqlite3 can't open it to
        # checkpoint, and it's about to be replaced wholesale anyway. Skip quietly
        # instead of logging a misleading "file is not a database" warning.
        return
    try:
        con = sqlite3.connect(str(dest), timeout=10)
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        logger.warning("Could not open database to quiesce before restore: %s", exc)
        return
    try:
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        logger.warning("Could not checkpoint WAL before restore: %s", exc)
    finally:
        con.close()


def _swap_in_restored_db(src: Path, dest: Path, backup_path: Path | None) -> None:
    """Replace ``dest`` with ``src`` atomically. The restored file is staged
    beside ``dest`` (same filesystem) and moved in with ``os.replace``, so a
    failed copy can never leave a half-written live DB. If the move itself fails
    after clobbering ``dest``, the safety backup is copied back."""
    staged = dest.with_name(dest.name + ".restore-tmp")
    try:
        shutil.copyfile(src, staged)
        os.replace(staged, dest)
    except OSError:
        Path(staged).unlink(missing_ok=True)
        if backup_path is not None and backup_path.exists() and not dest.exists():
            shutil.copyfile(backup_path, dest)
        raise


def restore_database(content: bytes) -> None:
    """Validate an uploaded SQLite file and swap it in as the live database.

    The current database is copied to ``<db>.bak`` first, the live DB is quiesced
    (writers drained + WAL checkpointed), and the swap itself is atomic so a
    failed restore leaves the original database intact. Destructive — the UI
    confirms before calling this.
    """
    if not content.startswith(SQLITE_MAGIC):
        raise RestoreError("Uploaded file is not a SQLite database.")

    fd, tmp_name = tempfile.mkstemp(prefix="hafi-restore-", suffix=".db")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.write_bytes(content)
        _validate_restore_candidate(tmp)

        # Release SQLAlchemy's pooled connections, then quiesce the file itself
        # before we touch it.
        engine = dbsession.get_engine()
        if engine is not None:
            engine.dispose()
        dest = settings.database_file
        dest.parent.mkdir(parents=True, exist_ok=True)
        _quiesce_database(dest)

        backup_path: Path | None = None
        if dest.exists():
            backup_path = dest.with_name(dest.name + ".bak")
            shutil.copy2(dest, backup_path)

        _swap_in_restored_db(tmp, dest, backup_path)

        # Drop stale WAL/SHM so the restored file is authoritative.
        for suffix in ("-wal", "-shm"):
            Path(str(dest) + suffix).unlink(missing_ok=True)

        _reconcile_encryption_after_restore()
        logger.info("Database restored from upload (%s bytes)", len(content))
    finally:
        tmp.unlink(missing_ok=True)


def _reconcile_encryption_after_restore() -> None:
    """Rebuild the engine (and clear the encryption marker) to match the restored file.

    The restored candidate is always plaintext (``SQLITE_MAGIC`` + a stdlib-sqlite
    integrity check gate ``restore_database``). If the install was at-rest encrypted,
    the still-SQLCipher engine would now run ``PRAGMA key`` against a plaintext file
    (HTTP 500), and the still-enabled ``encryption.json`` marker would make the app
    lock itself over a perfectly valid DB on the next restart. So drop the marker /
    stored key and reconfigure the engine to plaintext, mirroring how
    ``disable_encryption`` calls ``dbsession.configure(None)`` after its own swap.

    On a plaintext install this simply rebuilds a fresh plaintext engine instead of
    leaning on the disposed one silently reconnecting.
    """
    from app.services import security_service

    if security_service.read_marker() is not None:
        security_service._delete_marker()
        security_service.clear_stored_key()  # the auto-unlock key no longer opens anything
        logger.info(
            "Restored a plaintext database over an encrypted install; "
            "at-rest encryption has been disabled to match the restored file."
        )
    dbsession.configure(None)


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
#
# The export keys every entity by a PORTABLE name (category by ``name``, vendor by
# ``canonical_name``, project by ``name``) rather than by its local integer id,
# which is meaningless on another instance. The two maps below name the rule
# condition/action types whose stored value is a local row id that must therefore
# be translated to a name on export and back to a local id on import. Everything
# else (merchant_contains, amount_between, set_country, mark_transfer, ...) carries
# a literal value that is already portable, so it passes through untouched. Keeping
# them as one discriminator each keeps export and import symmetric.
_RULE_REF_CONDITIONS: dict[str, str] = {
    "vendor_equals": "vendor",
    "category_equals": "category",
}
_RULE_REF_ACTIONS: dict[str, str] = {
    "set_vendor": "vendor",
    "set_category": "category",
    "set_project": "project",
}


def _ref_id_to_name(name_by_id: dict[int, str], value: str | None) -> str | None:
    """Translate a stored local-id reference to its portable name for export.

    Returns ``None`` when the value is unset or its target no longer exists (a
    stale reference), and carries a non-integer value through unchanged. A rule
    whose reference resolves to ``None`` will simply be skipped on import.
    """
    if value is None:
        return None
    try:
        return name_by_id.get(int(value))
    except (TypeError, ValueError):
        return value


def _export_rule(rule: Rule, ref_names: dict[str, dict[int, str]]) -> dict:
    """Serialise a rule, translating any referential id to a portable name."""
    condition_value = rule.condition_value
    cond_kind = _RULE_REF_CONDITIONS.get(rule.condition_type)
    if cond_kind is not None:
        condition_value = _ref_id_to_name(ref_names[cond_kind], rule.condition_value)

    action_value = rule.action_value
    act_kind = _RULE_REF_ACTIONS.get(rule.action_type)
    if act_kind is not None:
        action_value = _ref_id_to_name(ref_names[act_kind], rule.action_value)

    return {
        "name": rule.name,
        "priority": rule.priority,
        "enabled": rule.enabled,
        "condition_type": rule.condition_type,
        "condition_value": condition_value,
        "action_type": rule.action_type,
        "action_value": action_value,
        "created_from": rule.created_from,
    }


def export_config(db: Session) -> dict:
    """Export settings + category/vendor/rule library as a portable JSON document."""
    categories = db.scalars(select(Category)).all()
    vendors = db.scalars(select(Vendor)).all()
    aliases = db.scalars(select(VendorAlias)).all()
    rules = db.scalars(select(Rule)).all()
    aliases_by_vendor: dict[int, list[dict]] = {}
    for a in aliases:
        aliases_by_vendor.setdefault(a.vendor_id, []).append(
            {"alias": a.alias, "match_type": a.match_type, "source": a.source}
        )
    # id -> portable-name lookups so a vendor's default category and every rule's
    # referential value export as names (built once, so no N+1 per row).
    category_name_by_id = {c.id: c.name for c in categories}
    ref_names: dict[str, dict[int, str]] = {
        "category": category_name_by_id,
        "vendor": {v.id: v.canonical_name for v in vendors},
        "project": {p.id: p.name for p in db.scalars(select(Project)).all()},
    }
    return {
        "version": "0.2",
        # The AI API key is a secret stored (encrypted) in a settings row — never
        # include it in a portable export (it wouldn't decrypt elsewhere anyway,
        # and would leak the raw key on an instance with no HAFI_DB_KEY set).
        "settings": [
            {"key": s.key, "value": s.value}
            for s in db.scalars(select(Setting)).all()
            if s.key != settings_service.AI_API_KEY
        ],
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
                # Portable name of the vendor's default category (or null when unset)
                # so the local FK id is not carried across instances.
                "default_category": category_name_by_id.get(v.default_category_id),
                "aliases": aliases_by_vendor.get(v.id, []),
            }
            for v in vendors
        ],
        "rules": [_export_rule(r, ref_names) for r in rules],
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
    """Upsert vendors (and their aliases) by canonical name; return count added.

    Categories are imported before vendors, so a vendor's ``default_category`` name
    can be resolved to a local category id here. An absent name leaves the FK NULL
    rather than writing a dangling reference. Vendors that already exist by canonical
    name are left untouched (only newly-added vendors get the default-category link).
    """
    added = 0
    existing_vendors = {v.canonical_name: v for v in db.scalars(select(Vendor)).all()}
    categories_by_name = {c.name: c.id for c in db.scalars(select(Category)).all()}
    for entry in data.get("vendors", []):
        if entry["canonical_name"] in existing_vendors:
            continue
        default_category_name = entry.get("default_category")
        vendor = Vendor(
            household_id=household_id,
            canonical_name=entry["canonical_name"],
            display_name=entry.get("display_name"),
            default_category_id=categories_by_name.get(default_category_name)
            if default_category_name
            else None,
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


def _import_rules(
    db: Session, data: dict, household_id: int
) -> tuple[int, list[str]]:
    """Upsert rules by name; return ``(added, sorted_skipped_names)``.

    Categories, vendors and projects are already present (categories/vendors were
    imported first; projects are matched against whatever exists locally), so each
    rule's referential value can be resolved from its portable name back to a local
    id. A rule whose referenced entity is absent locally is skipped entirely and
    reported rather than written with a dangling foreign key (mirrors the #558
    stale-rule guard). Existing rules with the same name are left untouched.
    """
    added = 0
    skipped_names: list[str] = []
    existing_names = {r.name for r in db.scalars(select(Rule)).all()}
    ref_ids: dict[str, dict[str, int]] = {
        "category": {c.name: c.id for c in db.scalars(select(Category)).all()},
        "vendor": {v.canonical_name: v.id for v in db.scalars(select(Vendor)).all()},
        "project": {p.name: p.id for p in db.scalars(select(Project)).all()},
    }

    for entry in data.get("rules", []):
        name = entry["name"]
        if name in existing_names:
            continue

        condition_value = entry.get("condition_value")
        cond_kind = _RULE_REF_CONDITIONS.get(entry["condition_type"])
        if cond_kind is not None:
            resolved = ref_ids[cond_kind].get(condition_value)
            if resolved is None:
                skipped_names.append(name)
                continue
            condition_value = str(resolved)

        action_value = entry.get("action_value")
        act_kind = _RULE_REF_ACTIONS.get(entry["action_type"])
        if act_kind is not None:
            resolved = ref_ids[act_kind].get(action_value)
            if resolved is None:
                skipped_names.append(name)
                continue
            action_value = str(resolved)

        db.add(
            Rule(
                household_id=household_id,
                name=name,
                priority=entry.get("priority", 100),
                enabled=entry.get("enabled", True),
                condition_type=entry["condition_type"],
                condition_value=condition_value,
                action_type=entry["action_type"],
                action_value=action_value,
                created_from="import",
            )
        )
        # Guard against a duplicate name appearing twice within the same document.
        existing_names.add(name)
        added += 1

    return added, sorted(skipped_names)


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
        # Flush so the just-added categories are visible to the vendor default-category
        # and rule reference lookups (the session runs with autoflush off).
        db.flush()
        vendors_added = _import_vendors(db, data, household.id)
        db.flush()
        # Rules after vendors/categories (and projects) so their referential values
        # can be resolved to local ids; a v0.1 document simply has no "rules" key.
        rules_added, skipped_rule_names = _import_rules(db, data, household.id)
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
        "rules_added": rules_added,
        "rules_skipped": len(skipped_rule_names),
        "skipped_rule_names": skipped_rule_names,
        "settings_set": settings_result["settings_set"],
        "settings_skipped": settings_result["settings_skipped"],
        "skipped_setting_keys": settings_result["skipped_setting_keys"],
    }
