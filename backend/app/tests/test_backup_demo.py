"""Tests for backup/restore + demo data (spec §26.5; backlog #9, #10, #16)."""

from __future__ import annotations

import json


def test_demo_load_is_idempotent(client):
    first = client.post("/api/backup/demo").json()
    assert first["new"] > 0
    total_after_first = client.get("/api/transactions").json()["total"]
    assert total_after_first == first["new"]

    # Re-loading must not duplicate.
    second = client.post("/api/backup/demo").json()
    assert second["new"] == 0
    assert second["duplicates"] == first["new"]
    assert client.get("/api/transactions").json()["total"] == total_after_first


def test_database_backup_download(client):
    client.post("/api/backup/demo")
    res = client.get("/api/backup/database")
    assert res.status_code == 200
    assert res.content.startswith(b"SQLite format 3\x00")


def test_database_restore_roundtrip(client):
    client.post("/api/backup/demo")
    total = client.get("/api/transactions").json()["total"]
    snapshot = client.get("/api/backup/database").content

    res = client.post(
        "/api/backup/restore",
        files={"file": ("backup.db", snapshot, "application/octet-stream")},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "restored"
    assert client.get("/api/transactions").json()["total"] == total


def test_restore_rejects_non_sqlite(client):
    res = client.post(
        "/api/backup/restore",
        files={"file": ("bad.db", b"this is not a database", "application/octet-stream")},
    )
    assert res.status_code == 400
    assert "SQLite" in res.json()["detail"]


def test_config_export_and_import(client):
    export = client.get("/api/backup/config").json()
    assert len(export["categories"]) == 22  # seeded library
    # Re-importing the same export is a no-op (everything already present).
    same = client.post(
        "/api/backup/config",
        files={"file": ("config.json", json.dumps(export).encode(), "application/json")},
    ).json()
    assert same["categories_added"] == 0

    # Importing a config with a new category + vendor adds them.
    cfg = {
        "categories": [{"name": "Holiday Fund", "colour": "#00BCD4"}],
        "vendors": [
            {"canonical_name": "Octopus Energy", "aliases": [{"alias": "OCTOPUS", "match_type": "contains"}]}
        ],
        "settings": [{"key": "demo_setting", "value": "1"}],
    }
    added = client.post(
        "/api/backup/config",
        files={"file": ("config.json", json.dumps(cfg).encode(), "application/json")},
    ).json()
    assert added["categories_added"] == 1
    assert added["vendors_added"] == 1
    assert added["settings_set"] == 1

    names = {c["name"] for c in client.get("/api/categories").json()}
    assert "Holiday Fund" in names
    assert any(v["canonical_name"] == "Octopus Energy" for v in client.get("/api/vendors").json())
