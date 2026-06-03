"""Settings → Services control panel (backlog §38): one place to see/toggle
AI, OCR and online FX; MQTT shown read-only (it's add-on-configured)."""

from __future__ import annotations


def test_services_status_shape_and_defaults(client):
    s = client.get("/api/settings/services").json()
    assert set(s) == {"ai", "ocr", "fx", "mqtt"}
    assert s["ocr"]["enabled"] is True          # OCR on by default
    assert s["fx"]["enabled"] is False          # manual rates by default (no network)
    assert s["ai"]["configurable"] is True
    assert s["mqtt"]["configurable"] is False   # configured in the add-on, not here


def test_ai_on_off_reflects_real_modes(client):
    # no_ai and strict_local are BOTH AI-off in the engine, so the panel shows Off.
    client.put("/api/settings", json={"privacy_mode": "no_ai"})
    assert client.get("/api/settings/services").json()["ai"]["enabled"] is False
    client.put("/api/settings", json={"privacy_mode": "strict_local"})
    assert client.get("/api/settings/services").json()["ai"]["enabled"] is False
    # A real mode reads On (active), even before a provider is configured.
    client.put("/api/settings", json={"privacy_mode": "cloud_manual"})
    ai = client.get("/api/settings/services").json()["ai"]
    assert ai["enabled"] is True and ai["mode"] == "cloud_manual" and ai["configured"] is False


def test_ocr_detail_reflects_toggle(client):
    client.put("/api/settings", json={"ocr_enabled": False})
    assert client.get("/api/settings/services").json()["ocr"]["detail"].startswith("Off")
    client.put("/api/settings", json={"ocr_enabled": True})
    assert client.get("/api/settings/services").json()["ocr"]["detail"].startswith("On")


def test_fx_toggle(client):
    client.put("/api/settings", json={"fx_mode": "frankfurter"})
    assert client.get("/api/settings/services").json()["fx"]["enabled"] is True
    client.put("/api/settings", json={"fx_mode": "manual"})
    assert client.get("/api/settings/services").json()["fx"]["enabled"] is False


def test_ocr_toggle_status(client):
    client.put("/api/settings", json={"ocr_enabled": False})
    assert client.get("/api/settings/services").json()["ocr"]["enabled"] is False
    client.put("/api/settings", json={"ocr_enabled": True})
    assert client.get("/api/settings/services").json()["ocr"]["enabled"] is True


def test_run_ocr_skips_when_disabled(db):
    """With OCR turned off, run_ocr short-circuits to 'skipped' (manual entry) even
    before touching the file — distinct from the 'failed' path for a missing file."""
    from app.models import Receipt
    from app.services import receipt_service, settings_service

    settings_service.set_value(db, settings_service.OCR_ENABLED, "false")
    receipt = Receipt(source_filename="x.png", ocr_status="not_processed")
    db.add(receipt)
    db.commit()

    receipt_service.run_ocr(db, receipt, auto_match=False)
    assert receipt.ocr_status == "skipped"
