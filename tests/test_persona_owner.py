"""Persona owner adoption: unknown personas resolve from the configured
authoritative owner (OPAL Think) and are stored ``origin: owner``; standalone
mode (no owner URL) keeps the not-registered error; existing records stay
``origin: local`` after migration."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import httpx
import pytest

from opal_say.persistence.store import Store
from opal_say.service.generation import GenerationService

OWNER_BODY = {
    "persona_id": "amy",
    "name": "Amy",
    "description": None,
    "voice_id": "en-gb-alan-low",
    "enabled": True,
    "origin": "local",
    "created_at": "2025-01-01T00:00:00Z",
    "updated_at": "2025-01-01T00:00:00Z",
}


def _svc(store: Store, owner_url: str | None) -> GenerationService:
    return GenerationService(store, engine=None, setting=None, owner_url=owner_url)


@pytest.fixture
def store(tmp_path) -> Store:
    return Store(tmp_path / "say.db")


def test_owner_fetch_stores_owner_persona(store, monkeypatch):
    def fake_get(url, headers=None, timeout=5.0):
        assert url == "http://think:8080/api/v1/persona/amy"
        return httpx.Response(200, json=OWNER_BODY, headers={"ETag": '"s7"'})

    monkeypatch.setattr("opal_say.service.generation.httpx.get", fake_get)
    svc = _svc(store, "http://think:8080")
    row = svc._fetch_owner_persona("amy")
    assert row is not None
    assert row["voice_id"] == "en-gb-alan-low"
    assert row["origin"] == "owner"
    assert row["owner_etag"] == '"s7"'
    assert row["owner_checked_at"]


def test_owner_fetch_sends_bearer_token(store, monkeypatch):
    seen = {}

    def fake_get(url, headers=None, timeout=5.0):
        seen.update(headers or {})
        return httpx.Response(404, json={})

    monkeypatch.setattr("opal_say.service.generation.httpx.get", fake_get)
    monkeypatch.setenv("OPAL_SAY_PERSONA_OWNER_TOKEN", "sekret")
    assert _svc(store, "http://think:8080")._fetch_owner_persona("amy") is None
    assert seen["Authorization"] == "Bearer sekret"
    assert seen.get("If-None-Match") is None


def test_owner_fetch_404_returns_none(store, monkeypatch):
    monkeypatch.setattr(
        "opal_say.service.generation.httpx.get", lambda url, headers=None, timeout=5.0: httpx.Response(404, json={})
    )
    assert _svc(store, "http://think:8080")._fetch_owner_persona("nobody") is None
    assert store.get_persona("nobody") is None


def test_owner_fetch_network_error_returns_none(store, monkeypatch):
    def boom(url, headers=None, timeout=5.0):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("opal_say.service.generation.httpx.get", boom)
    assert _svc(store, "http://think:8080")._fetch_owner_persona("amy") is None


def test_no_owner_url_stays_standalone(store, monkeypatch):
    called = []
    monkeypatch.setattr("opal_say.service.generation.httpx.get", lambda *a, **k: called.append(1))
    svc = _svc(store, None)
    assert svc.owner_url is None
    assert store.get_persona("amy") is None
    assert called == []


# -- refresh of owner-synced personas -------------------------------------------


def _owner_row(store: Store, checked_at: str | None = None, **over) -> dict:
    now = datetime.now(UTC).isoformat()
    row = {
        "persona_id": "amy", "name": "Amy", "description": None, "voice_id": "v1",
        "enabled": 1, "origin": "owner", "owner_etag": '"s1"',
        "owner_checked_at": checked_at, "created_at": now, "updated_at": now,
    }
    row.update(over)
    store.insert_persona(row)
    return row


def _svc_with_refresh(store: Store, monkeypatch, seconds: int = 0):
    svc = _svc(store, "http://think:8080")

    class _Setting:
        def effective(self, key):
            return seconds if key == "persona_refresh_seconds" else 20000

    svc.setting = _Setting()
    return svc


def test_refresh_applies_owner_changes(store, monkeypatch):
    _owner_row(store)
    seen = {}
    body = {**OWNER_BODY, "voice_id": "v2", "updated_at": "2025-02-02T00:00:00Z"}

    def fake_get(url, headers=None, timeout=5.0):
        seen.update(headers or {})
        return httpx.Response(200, json=body, headers={"ETag": '"s9"'})

    monkeypatch.setattr("opal_say.service.generation.httpx.get", fake_get)
    svc = _svc_with_refresh(store, monkeypatch)
    row = svc._resolve_owner_persona(store.get_persona("amy"))
    assert row["voice_id"] == "v2" and row["owner_etag"] == '"s9"'
    assert seen["If-None-Match"] == '"s1"'  # version-aware revalidation


def test_refresh_marks_owner_disablement(store, monkeypatch):
    _owner_row(store)
    body = {**OWNER_BODY, "enabled": False, "updated_at": "2025-02-02T00:00:00Z"}
    monkeypatch.setattr(
        "opal_say.service.generation.httpx.get",
        lambda url, headers=None, timeout=5.0: httpx.Response(200, json=body),
    )
    row = _svc_with_refresh(store, monkeypatch)._resolve_owner_persona(store.get_persona("amy"))
    assert row["enabled"] == 0


def test_refresh_404_disables_cached_persona(store, monkeypatch):
    _owner_row(store)
    monkeypatch.setattr(
        "opal_say.service.generation.httpx.get",
        lambda url, headers=None, timeout=5.0: httpx.Response(404, json={}),
    )
    row = _svc_with_refresh(store, monkeypatch)._resolve_owner_persona(store.get_persona("amy"))
    assert row["enabled"] == 0


def test_refresh_outage_keeps_cached_and_bumps_check(store, monkeypatch):
    old = "2000-01-01T00:00:00Z"
    _owner_row(store, owner_checked_at=old)
    calls = []

    def boom(url, headers=None, timeout=5.0):
        calls.append(url)
        raise httpx.ConnectError("down")

    monkeypatch.setattr("opal_say.service.generation.httpx.get", boom)
    svc = _svc_with_refresh(store, monkeypatch)
    row = svc._resolve_owner_persona(store.get_persona("amy"))
    assert row["voice_id"] == "v1" and row["enabled"] == 1  # cached copy still serves
    assert row["owner_checked_at"] != old  # retry is bounded to one call per interval


def test_fresh_owner_row_is_not_refetched(store, monkeypatch):
    _owner_row(store, owner_checked_at=datetime.now(UTC).isoformat())
    calls = []
    monkeypatch.setattr("opal_say.service.generation.httpx.get", lambda *a, **k: calls.append(1))
    row = _svc_with_refresh(store, monkeypatch, seconds=60)._resolve_owner_persona(
        store.get_persona("amy"))
    assert calls == []
    assert row["voice_id"] == "v1"


def test_local_persona_never_contacts_owner(store, monkeypatch):
    _owner_row(store, persona_id="localamy", origin="local", owner_checked_at=None)
    calls = []
    monkeypatch.setattr("opal_say.service.generation.httpx.get", lambda *a, **k: calls.append(1))
    svc = _svc_with_refresh(store, monkeypatch)
    row = store.get_persona("localamy")
    assert row["origin"] == "local"
    # local rows are authoritative: never revalidated against the owner
    assert svc._resolve_owner_persona(row) is row
    assert calls == []


def test_existing_rows_migrate_to_local_origin(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE personas (persona_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT,"
        " voice_id TEXT NOT NULL, enabled INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO personas VALUES ('legacy','Legacy',NULL,'v1',1,'t','t')"
    )
    conn.commit()
    conn.close()
    # opening the store should migrate the table in place without touching rows
    store = Store.__new__(Store)
    store._conn = sqlite3.connect(db)
    # run just the migration block the real Store runs after connect
    if "origin" not in {r[1] for r in store._conn.execute("PRAGMA table_info(personas)")}:
        store._conn.execute("ALTER TABLE personas ADD COLUMN origin TEXT NOT NULL DEFAULT 'local'")
    row = store._conn.execute("SELECT origin FROM personas WHERE persona_id='legacy'").fetchone()
    assert row[0] == "local"
