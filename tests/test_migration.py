from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opal_say.api.app import create_app
from opal_say.config import ServiceConfig
from opal_say.contract import load_contract


def test_bundle_has_no_dangling_references():
    spec = load_contract()

    def check(node):
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                assert ref.startswith("#/")
                target = spec
                for part in ref[2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            for value in node.values():
                check(value)
        elif isinstance(node, list):
            for value in node:
                check(value)

    check(spec)


@pytest.mark.parametrize("tag", ['r0', 'W/"r0"', '*', '"r0","r1"'])
def test_strong_preconditions_reject_malformed(app, tag):
    with TestClient(app) as client:
        before = client.get("/api/v1/engine/fake").json()
        response = client.patch("/api/v1/engine/fake", json={"enabled": False}, headers={"If-Match": tag})
        assert response.status_code == 422
        assert client.get("/api/v1/engine/fake").json() == before


def test_opaque_stale_tag_is_precondition_failure(app):
    with TestClient(app) as client:
        assert client.patch("/api/v1/engine/fake", json={"enabled": False},
                            headers={"If-Match": '"opaque"'}).status_code == 412


def test_mutation_revision_and_cors(app):
    with TestClient(app) as client:
        tag = client.get("/api/v1/setting/").headers["etag"]
        response = client.patch("/api/v1/engine/fake", json={"enabled": False}, headers={"If-Match": tag})
        assert response.status_code == 200
        assert response.headers["etag"] != tag
        response = client.get("/api/v1/setting/", headers={"Origin": "http://localhost:5173"})
        assert "etag" in response.headers["access-control-expose-headers"].lower()


def test_authorization(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_READ_TOKEN", "reader-fixture")
    monkeypatch.setenv("TEST_ADMIN_TOKEN", "admin-fixture")
    cfg = ServiceConfig(data_dir=tmp_path / "var", config_dir=tmp_path / "config",
                        auth_enabled=True, token_env="TEST_READ_TOKEN", admin_token_env="TEST_ADMIN_TOKEN")
    with TestClient(create_app(cfg, engines=[])) as client:
        response = client.get("/api/v1/setting/")
        assert response.status_code == 401
        assert response.json()["error"]["request_id"] == response.headers["x-opal-request-id"]
        read = {"Authorization": "Bearer reader-fixture"}
        admin = {"Authorization": "Bearer admin-fixture"}
        tag = client.get("/api/v1/setting/", headers=read).headers["etag"]
        response = client.put("/api/v1/setting/default_speed", json={"value": 1.2},
                              headers={**read, "If-Match": tag})
        assert response.status_code == 403
        assert client.get("/api/v1/setting/", headers=read).headers["etag"] == tag
        response = client.put("/api/v1/setting/default_speed", json={"value": 1.2},
                              headers={**admin, "If-Match": tag})
        assert response.status_code == 200
        assert "admin-fixture" not in response.text


def test_persona_aliases_and_streams(app):
    import json

    with TestClient(app) as client:
        def write(method, path, body):
            return client.request(method, path, json=body,
                                  headers={"If-Match": client.get("/api/v1/setting/").headers["etag"]})

        for voice_id, ref in (("tone", "tone-a"), ("other", "tone-b")):
            assert write("POST", "/api/v1/voice/", {"voice_id": voice_id, "name": voice_id, "engine_id": "fake",
                                                    "engine_voice_ref": ref}).status_code == 201
        assert write("POST", "/api/v1/persona/", {"persona_id": "unbound", "name": "Unbound"}).status_code == 201
        assert write("POST", "/api/v1/persona/", {
            "persona_id": "speaker", "name": "Speaker", "voice_id": "other"}).status_code == 201
        assert write("POST", "/api/v1/profile/", {"id": "default", "name": "Default", "is_active": True,
                     "modules": {"opal-say": {"voice_id": "tone", "sample_rate": 16000}}}).status_code == 201
        for selection, expected in (({"persona_id": "speaker"}, "other"), ({"persona": "speaker"}, "other"),
                                    ({"persona_id": "speaker", "voice_id": "tone"}, "tone"),
                                    ({"persona_id": "unbound"}, "tone")):
            body = {"text": "Persona speech.", **selection}
            for path in ("/api/v1/generate", "/api/v1/generate/stream"):
                response = client.post(path, json=body)
                assert response.status_code == 200, response.text
                assert response.headers["x-opal-voice-id"] == expected
                assert response.headers["x-opal-sample-rate"] == "16000"
                assert response.content
            payload = json.dumps(selection) + '\n' + json.dumps({"text": "Stream speech.", "flush": True}) + '\n{"done":true}\n'
            response = client.post("/api/v1/generate/stream/incremental", content=payload,
                                   headers={"Content-Type": "application/x-ndjson"})
            assert response.status_code == 200 and response.content
            assert response.headers["x-opal-voice-id"] == expected
        assert client.post("/api/v1/generate", json={
            "text": "bad", "persona_id": "speaker", "persona": "unbound"}).status_code == 422
        assert write("PATCH", "/api/v1/persona/speaker", {"voice_id": None}).json()["voice_id"] is None
        assert client.post("/api/v1/persona/speaker/validate").status_code == 200


def test_owner_conflict_preserves_local_and_context(app, monkeypatch):
    import httpx

    observed = []

    def owner(url, *, headers, timeout):
        observed.append(headers)
        return httpx.Response(200, json={"persona_id": "speaker", "name": "Owner", "voice_id": "other", "enabled": True},
                              headers={"ETag": '"owner1"'})

    monkeypatch.setattr("opal_say.service.generation.httpx.get", owner)
    with TestClient(app) as client:
        tag = client.get("/api/v1/setting/").headers["etag"]
        response = client.post("/api/v1/persona/", json={"persona_id": "speaker", "name": "Local"}, headers={"If-Match": tag})
        assert response.status_code == 201
        app.state.services.generation.owner_url = "http://owner.test"
        response = client.get("/api/v1/persona/speaker", headers={
            "X-Opal-Transaction-Id": "transaction", "X-Opal-Request-Id": "parent"})
        assert response.status_code == 200 and response.json()["origin"] == "owner"
        assert response.json()["name"] == "Owner"
        assert app.state.services.store.get_persona("speaker")["name"] == "Local"
        assert observed[-1]["X-Opal-Causation-Id"] == "parent"
        assert observed[-1]["X-Opal-Transaction-Id"] == "transaction"
        assert observed[-1]["X-Opal-Request-Id"] != "parent"
        assert "X-Opal-Session-Id" not in observed[-1]
        assert client.post("/api/v1/generate", json={"text": "Bad binding", "persona_id": "speaker"}).status_code == 422

        def offline(*args, **kwargs):
            raise httpx.ConnectError("offline")

        monkeypatch.setattr("opal_say.service.generation.httpx.get", offline)
        assert client.get("/api/v1/persona/speaker").json()["name"] == "Owner"
        app.state.services.generation.owner_url = None
        assert client.get("/api/v1/persona/speaker").json()["name"] == "Local"


def test_namespaced_profiles(app):
    with TestClient(app) as client:
        def write(method, path, body):
            return client.request(method, path, json=body,
                                  headers={"If-Match": client.get("/api/v1/setting/").headers["etag"]})

        assert write("POST", "/api/v1/voice/", {"voice_id": "tone", "name": "Tone", "engine_id": "fake",
                                                "engine_voice_ref": "tone-a"}).status_code == 201
        response = write("POST", "/api/v1/profile/", {"id": "studio", "name": "Studio", "is_active": True,
                         "modules": {"opal-say": {"voice_id": "tone", "speed": 1.2, "volume": 1.5},
                                     "opal-listen": {}, "future-module": {"items": [1, 2]}}})
        assert response.status_code == 201, response.text
        profile = response.json()
        assert profile["id"] == "studio" and profile["shared"] == {}
        assert "voice_id" not in profile
        response = write("PATCH", "/api/v1/profile/studio", {"modules": {"opal-say": {"speed": None}}, "is_active": False})
        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["is_active"] is True
        assert updated["modules"]["opal-say"] == {"voice_id": "tone", "volume": 1.5}
        assert updated["modules"]["future-module"] == {"items": [1, 2]}
        before = client.get("/api/v1/profile/studio").json()
        for body in ({"modules": {"opal-listen": {}}}, {"shared": {"speed": 2}},
                     {"modules": {"opal-say": {"speed": 99}}}):
            assert write("PATCH", "/api/v1/profile/studio", body).status_code == 422
            assert client.get("/api/v1/profile/studio").json() == before
        audio = client.post("/api/v1/generate", json={"text": "Namespaced speech.", "profile_id": "studio"})
        assert audio.status_code == 200, audio.text
        assert audio.headers["x-opal-profile-id"] == "studio"
        assert write("POST", "/api/v1/profile/", {"name": "Empty"}).status_code == 201
        assert write("DELETE", "/api/v1/profile/studio", None).status_code == 409


def test_previous_profile_database_upgrade_and_recovery(tmp_path, monkeypatch):
    import sqlite3

    from opal_say.persistence.store import Store

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE profiles (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT, "
                           "is_active INTEGER NOT NULL, voice_id TEXT NOT NULL, speed REAL NOT NULL, volume REAL NOT NULL, "
                           "output_format TEXT NOT NULL, sample_rate INTEGER, engine_options TEXT NOT NULL, "
                           "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        connection.execute("INSERT INTO profiles VALUES (7, 'Old', NULL, 1, 'tone', 1.25, 0.8, 'pcm', 16000, '{}', "
                           "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')")
        connection.execute("CREATE TABLE personas (persona_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, "
                           "voice_id TEXT NOT NULL, enabled INTEGER NOT NULL, created_at TEXT NOT NULL, "
                           "updated_at TEXT NOT NULL)")
        connection.execute("INSERT INTO personas VALUES ('old', 'Old persona', NULL, 'tone', 1, "
                           "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')")
    original = Store._insert

    def fail(connection, table, values):
        if table == "profiles_next":
            raise RuntimeError("injected upgrade failure")
        return original(connection, table, values)

    with monkeypatch.context() as patch:
        patch.setattr(Store, "_insert", staticmethod(fail))
        with pytest.raises(RuntimeError, match="injected"):
            Store(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT voice_id FROM profiles WHERE id=7").fetchone() == ("tone",)
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='profiles_next'").fetchone() is None
    store = Store(path)
    record = store.get_profile("7")
    assert record["modules"]["opal-say"] == {"voice_id": "tone", "speed": 1.25, "volume": 0.8,
                                            "output_format": "pcm", "sample_rate": 16000, "engine_options": {}}
    assert record["is_active"] and record["shared"] == {}
    assert store.get_persona("old")["voice_id"] == "tone"
    store.update_persona("old", {"voice_id": None})
    store.close()
    reopened = Store(path)
    assert reopened.get_profile("7") == record
    assert reopened.get_persona("old")["voice_id"] is None
    reopened.close()


def test_registered_settings(app, monkeypatch):
    with TestClient(app) as client:
        def write(method, path, **kwargs):
            return client.request(method, path, headers={"If-Match": client.get("/api/v1/setting/").headers["etag"]}, **kwargs)

        path = "/api/v1/setting/default_speed"
        record = client.get(path).json()
        assert record["value"] == record["configured_value"] == record["default"] == 1.0
        assert record["source"] == "default" and not record["has_override"]
        assert client.get("/api/v1/setting/").json()["default_speed"] == 1.0
        assert write("PUT", path, json={"value": 1.5}).json()["source"] == "persisted"
        monkeypatch.setenv("OPAL_SAY_DEFAULT_SPEED", "2.0")
        record = write("PUT", path, json={"value": 1.25}).json()
        assert record["value"] == 2.0 and record["configured_value"] == 1.25
        assert record["source"] == "environment" and record["warnings"]
        assert not record["pending_restart"]
        assert write("DELETE", path).status_code == 204
        assert write("DELETE", path).status_code == 204
        assert client.get(path).json()["value"] == 2.0
        assert write("PUT", "/api/v1/setting/not_registered", json={"value": 5}).status_code == 404
        before = client.get(path).json()
        assert write("PUT", path, json={"value": 99}).status_code == 422
        assert client.get(path).json() == before
        assert client.patch("/api/v1/setting/", json={"default_speed": 1}).status_code == 405


def test_health_cutover(app):
    with TestClient(app) as client:
        response = client.get("/api/v1/health/")
        assert response.status_code == 200
        health = response.json()
        assert health["name"] == "opal-say-svc"
        assert health["live"] is True and health["ready"] is False
        assert "engines" in health["details"] and "engines" not in health
        assert client.get("/api/v1/health/live").status_code == 200
        assert client.get("/api/v1/health/ready").status_code == 503
        assert client.get("/api/v1/info/").status_code == 404
        assert client.get("/api/v1/info/ping").status_code == 404


def test_non_loopback_requires_authentication(tmp_path: Path):
    cfg = ServiceConfig(host="0.0.0.0", data_dir=tmp_path / "var")
    with pytest.raises(ValueError, match="authentication"):
        create_app(cfg, engines=[])
