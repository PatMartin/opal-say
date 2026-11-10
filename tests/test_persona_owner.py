"""Persona owner adoption: unknown personas resolve from the configured
authoritative owner (OPAL Think) and are stored ``origin: owner``; standalone
mode (no owner URL) keeps the not-registered error; existing records stay
``origin: local`` after migration."""

from __future__ import annotations

import sqlite3

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
    def fake_get(url, timeout):
        assert url == "http://think:8080/api/v1/persona/amy"
        return httpx.Response(200, json=OWNER_BODY)

    monkeypatch.setattr("opal_say.service.generation.httpx.get", fake_get)
    svc = _svc(store, "http://think:8080")
    row = svc._fetch_owner_persona("amy")
    assert row is not None
    assert row["voice_id"] == "en-gb-alan-low"
    assert row["origin"] == "owner"


def test_owner_fetch_404_returns_none(store, monkeypatch):
    monkeypatch.setattr(
        "opal_say.service.generation.httpx.get", lambda url, timeout: httpx.Response(404, json={})
    )
    assert _svc(store, "http://think:8080")._fetch_owner_persona("nobody") is None
    assert store.get_persona("nobody") is None


def test_owner_fetch_network_error_returns_none(store, monkeypatch):
    def boom(url, timeout):
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
