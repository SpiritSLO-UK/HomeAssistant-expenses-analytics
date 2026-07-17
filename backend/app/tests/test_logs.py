"""Activity-log (audit) viewer — owner-gating, filtering, and that the newly
wired events are recorded (Stage 12; backlog #92).

Identity is simulated with the HA ingress headers the middleware reads; with no
header a request resolves to the local single-user owner.
"""

from __future__ import annotations


def _hdr(uid: str, name: str | None = None) -> dict[str, str]:
    return {"X-Remote-User-Id": uid, "X-Remote-User-Display-Name": name or uid}


def _make_user(client, uid: str, name: str, *, role: str = "member") -> int:
    """Bootstrap the owner, surface a second HA user, and set their role."""
    client.get("/api/users/me")  # first request → local owner
    client.get("/api/users/me", headers=_hdr(uid, name))  # second user → pending
    user_id = next(u["id"] for u in client.get("/api/users").json() if u["external_id"] == uid)
    client.patch(f"/api/users/{user_id}", json={"role": role, "status": "approved"})
    return user_id


def test_activity_log_is_owner_only(client):
    _make_user(client, "ha-vi", "Vi", role="viewer")
    assert client.get("/api/logs/activity", headers=_hdr("ha-vi", "Vi")).status_code == 403
    assert client.get("/api/logs/actions", headers=_hdr("ha-vi", "Vi")).status_code == 403
    # A read/write member is still not the owner.
    _make_user(client, "ha-me", "Mem", role="member")
    assert client.get("/api/logs/activity", headers=_hdr("ha-me", "Mem")).status_code == 403


def test_owner_can_read_activity_log(client):
    client.get("/api/users/me")
    resp = client.get("/api/logs/activity")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_user_management_is_audited(client):
    _make_user(client, "ha-al", "Al")  # the PATCH records an update_user entry

    log = client.get("/api/logs/activity").json()
    actions = [e["action"] for e in log]
    assert "update_user" in actions

    entry = next(e for e in log if e["action"] == "update_user")
    assert entry["entity_type"] == "user"
    assert entry["actor"]  # the owner who made the change, not empty
    assert isinstance(entry["details"], dict)
    # Newest first.
    ids = [e["id"] for e in log]
    assert ids == sorted(ids, reverse=True)


def test_demo_load_is_audited(client):
    client.get("/api/users/me")
    client.post("/api/backup/demo")

    log = client.get("/api/logs/activity", params={"action": "load_demo"}).json()
    assert len(log) >= 1
    assert all(e["action"] == "load_demo" for e in log)
    assert isinstance(log[0]["details"], dict)  # the demo summary blob


def test_action_filter_narrows_results(client):
    _make_user(client, "ha-bo", "Bo")
    client.post("/api/backup/demo")

    only = client.get("/api/logs/activity", params={"action": "update_user"}).json()
    assert only  # there is at least the promotion entry
    assert all(e["action"] == "update_user" for e in only)


def test_actions_endpoint_lists_distinct_sorted(client):
    _make_user(client, "ha-cy", "Cy")
    actions = client.get("/api/logs/actions").json()
    assert "update_user" in actions
    assert actions == sorted(actions)
    assert len(actions) == len(set(actions))


def test_privacy_mode_change_is_recorded_as_decision(client):
    client.get("/api/users/me")  # owner
    r = client.put("/api/settings", json={"privacy_mode": "cloud_manual"})
    assert r.status_code == 200

    # The "decision" prefix still matches every decision kind (the 🔑 Decisions view).
    decisions = client.get("/api/logs/activity", params={"action": "decision"}).json()
    assert decisions, "an AI mode change should be logged as a decision"
    top = decisions[0]
    # Each kind is now its own namespaced, individually-filterable action.
    assert top["action"] == "decision:ai_mode"
    assert "AI mode changed" in top["details"]["summary"]
    assert top["details"]["to"] == "cloud_manual"
    assert top["actor"]  # the user who made the change
    assert "decision:ai_mode" in client.get("/api/logs/actions").json()
    # Filtering by the specific kind returns just that decision.
    only = client.get("/api/logs/activity", params={"action": "decision:ai_mode"}).json()
    assert only and all(d["action"] == "decision:ai_mode" for d in only)


def test_no_decision_logged_when_setting_unchanged(client):
    client.get("/api/users/me")
    # strict_local is the default → setting it again is a no-op, no decision.
    client.put("/api/settings", json={"privacy_mode": "strict_local"})
    assert client.get("/api/logs/activity", params={"action": "decision"}).json() == []


def test_ocr_toggle_is_recorded_as_decision(client):
    client.get("/api/users/me")
    client.put("/api/settings", json={"ocr_enabled": True})
    client.put("/api/settings", json={"ocr_enabled": False})
    decisions = client.get("/api/logs/activity", params={"action": "decision"}).json()
    assert any("OCR turned off" in d["details"]["summary"] for d in decisions)


def test_all_mutating_api_calls_are_audited(client):
    """Every mutating (non-GET) /api request is logged as a generic `api_call`
    entry with the actor + method + path + status (backlog: track all actions)."""
    client.get("/api/users/me")  # owner (a GET — must NOT be logged)
    client.post("/api/backup/demo")  # a mutating call → one api_call entry

    log = client.get("/api/logs/activity", params={"action": "api_call"}).json()
    assert log, "mutating calls should produce api_call audit entries"
    top = log[0]  # newest first → the demo POST
    assert top["actor"]  # actor resolved by the auth guard, not empty
    assert top["details"]["method"] == "POST"
    assert top["details"]["path"].startswith("/api/")
    assert top["details"]["status"] == 200
    # No GET was logged (reads are intentionally excluded as too noisy).
    assert all(e["details"]["method"] != "GET" for e in log)
    # The label shows up in the action-filter list.
    assert "api_call" in client.get("/api/logs/actions").json()


def test_api_call_row_is_scoped_to_the_actor_household(db):
    # A generic api_call audit row must carry a household scope (SR-E8): the
    # acting user's household when it can be resolved by display name.
    from app.models import Household, User
    from app.services import audit_service

    household = Household(name="H", currency="GBP", mode="single")
    db.add(household)
    db.flush()
    db.add(User(household_id=household.id, display_name="Al", role="owner"))
    db.flush()

    audit_service.record_api_action(
        db, actor="Al", method="POST", path="/api/x", status=200
    )
    db.commit()

    entry = next(e for e in audit_service.recent(db) if e.action == "api_call")
    assert entry.household_id == household.id


def test_api_call_row_falls_back_to_single_household(db):
    # With no user matching the actor, the row still gets a household scope from
    # the single household of this MVP rather than being written unscoped (SR-E8).
    from app.models import Household
    from app.services import audit_service

    household = Household(name="H", currency="GBP", mode="single")
    db.add(household)
    db.flush()

    audit_service.record_api_action(
        db, actor="system", method="DELETE", path="/api/y", status=204
    )
    db.commit()

    entry = next(e for e in audit_service.recent(db) if e.action == "api_call")
    assert entry.household_id == household.id


def test_api_call_household_id_can_be_passed_explicitly(db):
    # An explicit household_id wins over derivation (forward-compat for callers
    # that already know the scope).
    from app.models import Household
    from app.services import audit_service

    household = Household(name="H", currency="GBP", mode="single")
    db.add(household)
    db.flush()

    audit_service.record_api_action(
        db, actor="Al", method="PUT", path="/api/z", status=200,
        household_id=household.id,
    )
    db.commit()

    entry = next(e for e in audit_service.recent(db) if e.action == "api_call")
    assert entry.household_id == household.id


def test_recent_action_prefix_escapes_like_metacharacters(db):
    # A prefix containing LIKE wildcards must filter literally, not as a pattern
    # (SR-E8). "api" must not match via the "_" wildcard etc.
    from app.services import audit_service

    audit_service.record(db, action="user_update")
    audit_service.record(db, action="user%update")
    audit_service.record(db, action="userXupdate")
    db.commit()

    # "user_" literally matches only "user_update" (not "userXupdate" via _ wildcard).
    underscore = {e.action for e in audit_service.recent(db, action_prefix="user_")}
    assert underscore == {"user_update"}
    # "user%" literally matches only "user%update" (not the others via % wildcard).
    percent = {e.action for e in audit_service.recent(db, action_prefix="user%")}
    assert percent == {"user%update"}


def test_small_details_are_stored_verbatim(db):
    # A small/normal details dict round-trips unchanged (no truncation marker).
    from app.services import audit_service

    payload = {"method": "POST", "path": "/api/x", "status": 200}
    audit_service.record(db, action="small_details", details=payload)
    db.commit()

    entry = next(e for e in audit_service.recent(db) if e.action == "small_details")
    assert audit_service.to_dict(entry)["details"] == payload


def test_oversized_details_are_truncated_and_bounded(db):
    # A large details payload is capped to a compact marker so the audit row
    # stays bounded, and recording still succeeds (SR-E8).
    from app.services import audit_service

    big = {"blob": "x" * (audit_service.MAX_DETAILS_BYTES + 5000)}
    audit_service.record(db, action="big_details", details=big)
    db.commit()

    entry = next(e for e in audit_service.recent(db) if e.action == "big_details")
    # The stored blob is bounded well under the cap.
    assert len(entry.details_json) <= audit_service.MAX_DETAILS_BYTES
    stored = audit_service.to_dict(entry)["details"]
    assert stored["_truncated"] is True
    # The true (pre-truncation) size is recorded for context.
    assert stored["_bytes"] > audit_service.MAX_DETAILS_BYTES
