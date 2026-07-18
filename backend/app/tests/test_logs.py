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


def _seed_search_entries(db):
    """Three entries with distinct actions/actors/details for the filter tests."""
    from app.services import audit_service

    audit_service.record(
        db, action="update_user", actor="Alice", details={"role": "owner", "note": "PROMOTED"}
    )
    audit_service.record(
        db, action="load_demo", actor="Bob", details={"summary": "Demo data loaded"}
    )
    audit_service.record(db, action="api_call", actor="alfred", details={"path": "/api/x"})
    db.commit()


def test_recent_q_matches_action_and_details_case_insensitively(db):
    from app.services import audit_service

    _seed_search_entries(db)

    # Matches the action name, case-insensitively.
    by_action = {e.action for e in audit_service.recent(db, q="LOAD_demo")}
    assert by_action == {"load_demo"}
    # Matches inside the serialised details blob, case-insensitively.
    by_details = {e.action for e in audit_service.recent(db, q="promoted")}
    assert by_details == {"update_user"}
    # No match returns an empty list.
    assert audit_service.recent(db, q="zzz-nothing") == []


def test_recent_q_escapes_like_metacharacters(db):
    from app.services import audit_service

    audit_service.record(db, action="pct", details={"note": "100% done"})
    audit_service.record(db, action="plain", details={"note": "100 done"})
    db.commit()

    # "%" must match literally, not as a wildcard swallowing everything.
    actions = {e.action for e in audit_service.recent(db, q="100%")}
    assert actions == {"pct"}


def test_recent_actor_substring_is_case_insensitive(db):
    from app.services import audit_service

    _seed_search_entries(db)

    # "al" matches Alice and alfred (substring, any case) but not Bob.
    actors = {e.actor for e in audit_service.recent(db, actor="AL")}
    assert actors == {"Alice", "alfred"}
    assert audit_service.recent(db, actor="nobody") == []


def test_recent_date_range_is_inclusive(db):
    from datetime import date, datetime

    from app.services import audit_service

    _seed_search_entries(db)
    entries = audit_service.recent(db)
    # Pin timestamps to three known days so the range bounds are testable.
    days = [datetime(2026, 1, 1, 23, 59), datetime(2026, 1, 2, 0, 0), datetime(2026, 1, 3, 12, 0)]
    for entry, when in zip(entries, days, strict=True):
        entry.created_at = when
    db.commit()

    # Inclusive lower bound: the 23:59 entry on Jan 1 is in when from=Jan 1.
    from_jan1 = audit_service.recent(db, date_from=date(2026, 1, 1))
    assert len(from_jan1) == 3
    # from=Jan 2 drops the Jan 1 entry.
    from_jan2 = audit_service.recent(db, date_from=date(2026, 1, 2))
    assert {e.created_at.day for e in from_jan2} == {2, 3}
    # Inclusive upper bound: to=Jan 2 keeps the midnight Jan 2 entry.
    to_jan2 = audit_service.recent(db, date_to=date(2026, 1, 2))
    assert {e.created_at.day for e in to_jan2} == {1, 2}
    # A from+to window narrows to exactly one day.
    only_jan2 = audit_service.recent(db, date_from=date(2026, 1, 2), date_to=date(2026, 1, 2))
    assert [e.created_at.day for e in only_jan2] == [2]


def test_activity_endpoint_accepts_search_filters(client):
    _make_user(client, "ha-se", "Sea")  # records update_user (actor = owner)
    client.put("/api/settings", json={"privacy_mode": "cloud_manual"})  # a decision

    # q matches the decision summary text, case-insensitively.
    hits = client.get("/api/logs/activity", params={"q": "ai mode CHANGED"}).json()
    assert hits and all("AI mode changed" in e["details"]["summary"] for e in hits)
    assert client.get("/api/logs/activity", params={"q": "zzz-no-such"}).json() == []

    # actor narrows to entries by that (substring of a) display name.
    some_actor = hits[0]["actor"]
    by_actor = client.get(
        "/api/logs/activity", params={"actor": some_actor[:3].lower()}
    ).json()
    assert by_actor and all(some_actor[:3].lower() in (e["actor"] or "").lower() for e in by_actor)

    # A date window around today includes entries; a past-only window excludes all.
    assert client.get(
        "/api/logs/activity", params={"date_from": "2000-01-01", "date_to": "2099-12-31"}
    ).json()
    assert client.get("/api/logs/activity", params={"date_to": "2000-01-01"}).json() == []
    # Filters compose with the existing action prefix.
    combined = client.get(
        "/api/logs/activity", params={"action": "decision", "q": "cloud_manual"}
    ).json()
    assert combined and all(e["action"].startswith("decision") for e in combined)


def test_activity_endpoint_rejects_bad_date(client):
    client.get("/api/users/me")  # owner
    assert client.get("/api/logs/activity", params={"date_from": "not-a-date"}).status_code == 422


def test_audit_export_honours_search_filters(client):
    _make_user(client, "ha-xf", "Xf")  # records update_user entries
    client.post("/api/backup/demo")  # records load_demo + api_call

    body = client.get("/api/logs/audit/export.csv", params={"q": "load_demo"}).content.decode(
        "utf-8-sig"
    )
    lines = body.splitlines()
    assert len(lines) >= 2  # header + at least one row
    assert all("load_demo" in line for line in lines[1:])
    assert "update_user" not in body

    empty = client.get(
        "/api/logs/audit/export.csv", params={"date_to": "2000-01-01"}
    ).content.decode("utf-8-sig")
    assert empty.splitlines() == ["id,timestamp,actor,action,household,details"]


def test_small_details_are_stored_verbatim(db):
    # A small/normal details dict round-trips unchanged (no truncation marker).
    from app.services import audit_service

    payload = {"method": "POST", "path": "/api/x", "status": 200}
    audit_service.record(db, action="small_details", details=payload)
    db.commit()

    entry = next(e for e in audit_service.recent(db) if e.action == "small_details")
    assert audit_service.to_dict(entry)["details"] == payload


def test_export_audit_returns_rows_for_the_household(db):
    # export_audit reuses the recent() path and returns CSV-ready rows carrying the
    # household name and the already-capped details cell.
    from app.models import Household
    from app.services import audit_service

    household = Household(name="Home", currency="GBP", mode="single")
    db.add(household)
    db.flush()
    audit_service.record(
        db, action="update_user", actor="Al", details={"role": "owner"},
        household_id=household.id,
    )
    db.commit()

    rows = audit_service.export_audit(db)
    row = next(r for r in rows if r["action"] == "update_user")
    assert row["actor"] == "Al"
    assert row["household"] == "Home"
    assert "owner" in row["details"]  # the serialised details blob
    assert set(audit_service.AUDIT_EXPORT_HEADERS) <= set(row)


def test_export_audit_action_prefix_filters(db):
    from app.services import audit_service

    audit_service.record(db, action="update_user")
    audit_service.record(db, action="load_demo")
    db.commit()

    actions = {r["action"] for r in audit_service.export_audit(db, action_prefix="update")}
    assert actions == {"update_user"}


def test_audit_csv_escapes_cells(db):
    # A details cell with a comma/quote must be quoted by csv.writer, never injected
    # raw into the CSV structure.
    from app.services import audit_service

    rows = [
        {
            "id": 1, "timestamp": "2026-01-01T00:00:00", "actor": 'A,"B',
            "action": "x", "household": "Home", "details": '{"k": "a,b"}',
        }
    ]
    text = audit_service.audit_csv(rows)
    lines = text.splitlines()
    assert lines[0] == ",".join(audit_service.AUDIT_EXPORT_HEADERS)
    assert '"A,""B"' in text  # the comma/quote cell is quoted + doubled


def test_audit_export_endpoint_is_owner_only(client):
    _make_user(client, "ha-ex", "Ex", role="viewer")
    r = client.get("/api/logs/audit/export.csv", headers=_hdr("ha-ex", "Ex"))
    assert r.status_code == 403
    _make_user(client, "ha-em", "Em", role="member")
    assert client.get("/api/logs/audit/export.csv", headers=_hdr("ha-em", "Em")).status_code == 403


def test_audit_export_endpoint_returns_csv_download(client):
    _make_user(client, "ha-ow", "Ow")  # PATCH records an update_user entry
    r = client.get("/api/logs/audit/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment; filename=" in r.headers["content-disposition"]
    assert ".csv" in r.headers["content-disposition"]
    body = r.content.decode("utf-8-sig")
    assert body.splitlines()[0] == "id,timestamp,actor,action,household,details"
    assert "update_user" in body


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
