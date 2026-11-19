"""Kernel config/save, config/load and config/validate replace the retired backup interfaces."""

from __future__ import annotations

import copy
import json

import pytest
import yaml
from fastapi.testclient import TestClient

from conftest import FakeEngine, make_profile, make_voice
from opal_say.api.app import create_app
from opal_say.persistence.store import Store


@pytest.fixture
def populated(client):
    make_voice(client, "v1", engine_options={"pitch": 200})
    make_voice(client, "v2", ref="tone-b", description="second")
    profile = make_profile(client, "main", "v1", is_active=True, speed=1.5, engine_options={"pitch": 220})
    assert client.post("/api/v1/persona/", json={"persona_id": "speaker", "name": "Speaker", "voice_id": "v2"}).status_code == 201
    client.put("/api/v1/setting/chunk_max_chars", json={"value": 120})
    assert client.patch(f"/api/v1/profile/{profile['id']}", json={"modules": {"opal-say": {"volume": 0.5}}}).status_code == 200
    response = client.post("/api/v1/config/save", json={"destination": "response"})
    assert response.status_code == 200, response.text
    return response.json()["snapshot"]


@pytest.fixture
def seed_files(config):
    files = {
        "engines.yaml": {"engines": {"fake": {"enabled": True, "options": {"available": True}}}},
        "voices.yaml": {"voices": [{"voice_id": "tone", "name": "Tone", "engine_id": "fake", "engine_voice_ref": "tone-a"}]},
        "profiles.yaml": {"profiles": [{"id": "seed-default", "name": "Default", "is_active": True,
                           "modules": {"opal-say": {"voice_id": "tone", "speed": 1.5},
                                       "future-peer": {"values": [True, 2, None]}}}]},
        "settings.yaml": {"settings": {"chunk_max_chars": 250}},
    }
    config.config_dir.mkdir(parents=True)
    for name, document in files.items():
        (config.config_dir / name).write_text(yaml.safe_dump(document), encoding="utf-8")
    return files


def test_fresh_namespaced_seed_and_reopen_preserve_standalone_generation(config, seed_files):
    for attempt in range(2):
        app = create_app(config, engines=[FakeEngine()])
        try:
            with TestClient(app) as client:
                profile = client.get("/api/v1/profile/seed-default").json()
                assert profile["modules"]["future-peer"] == {"values": [True, 2, None]}
                assert client.get("/api/v1/setting/chunk_max_chars").json()["value"] == (250 if attempt == 0 else 300)
                generated = client.post("/api/v1/generate", json={"text": "Seeded offline speech."})
                assert generated.status_code == 200 and generated.content.startswith(b"RIFF")
                if attempt == 0:
                    revision = client.get("/api/v1/setting/").headers["etag"]
                    assert client.put("/api/v1/setting/chunk_max_chars", json={"value": 300},
                                      headers={"If-Match": revision}).status_code == 200
        finally:
            app.state.services.close()


@pytest.mark.parametrize("filename,document", [
    ("engines.yaml", {"engines": []}),
    ("engines.yaml", {"engines": {"fake": {"enabled": "yes"}}}),
    ("voices.yaml", {"voices": {"not": "an array"}}),
    ("voices.yaml", {"voices": [{"name": "missing fields"}]}),
    ("profiles.yaml", {"profiles": [{"name": "bad", "modules": {"opal-say": {"voice_id": "missing"}}}]}),
    ("settings.yaml", {"settings": [1, 2]}),
    ("settings.yaml", {"settings": {"default_speed": 99}}),
])
def test_invalid_bootstrap_applies_nothing_and_preserves_existing_database(config, seed_files, filename, document):
    path = config.config_dir / filename
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    app = create_app(config, engines=[FakeEngine()])
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/voice/").json() == []
            assert client.get("/api/v1/profile/").json() == []
            assert app.state.services.store.list_settings() == []
    finally:
        app.state.services.close()
    path.write_text(yaml.safe_dump(seed_files[filename]), encoding="utf-8")
    app = create_app(config, engines=[FakeEngine()])
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/profile/").json() == []
            assert client.get("/api/v1/voice/").json() == []
            revision = client.get("/api/v1/setting/").headers["etag"]
            response = client.post("/api/v1/voice/", json=seed_files["voices.yaml"]["voices"][0],
                                   headers={"If-Match": revision})
            assert response.status_code == 201, response.text
            assert client.post("/api/v1/generate", json={"text": "Recovered catalog.", "voice_id": "tone"}).status_code == 200
    finally:
        app.state.services.close()


def state(client):
    response = client.post("/api/v1/config/save", json={"destination": "response"})
    assert response.status_code == 200, response.text
    snapshot = response.json()["snapshot"]
    snapshot.pop("created_at")
    return snapshot, response.headers["etag"]


def test_save_is_desired_configuration_without_revision_change(client, populated, app):
    before = client.get("/api/v1/setting/").headers["etag"]
    response = client.post("/api/v1/config/save", json={"destination": "response"})
    assert response.status_code == 200 and response.headers["etag"] == before
    assert response.json()["managed"] is False
    snapshot = response.json()["snapshot"]
    assert snapshot["format_version"] == "1.0.0" and snapshot["module"] == "opal-say"
    assert snapshot["settings"] == {"chunk_max_chars": 120}
    assert set(snapshot["resources"]) == {"profiles", "voices", "personas", "engines"}
    assert all(set(engine) == {"engine_id", "enabled", "options"} for engine in snapshot["resources"]["engines"])
    assert "metadata" not in snapshot["resources"]["voices"][0]
    assert "owner_checked_at" not in snapshot["resources"]["personas"][0]
    assert not app.state.services.config_ops.snapshot_path.exists()


def test_managed_roundtrip_and_response_does_not_replace_it(client, populated, app):
    saved = client.post("/api/v1/config/save")
    assert saved.status_code == 200 and saved.json()["managed"] is True
    path = app.state.services.config_ops.snapshot_path
    managed = path.read_bytes()
    original, old = state(client)
    client.put("/api/v1/setting/default_speed", json={"value": 2.0})
    client.patch("/api/v1/persona/speaker", json={"name": "Changed"})
    assert client.post("/api/v1/config/save", json={"destination": "response"}).status_code == 200
    assert path.read_bytes() == managed
    loaded = client.post("/api/v1/config/load")
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["applied"] is True
    assert loaded.headers["etag"] != old
    assert state(client)[0] == original
    assert path.read_bytes() == managed
    assert client.post("/api/v1/generate", json={"text": "Restored speech."}).status_code == 200


@pytest.mark.parametrize("mutation,code", [
    (lambda s: s.update(module="other"), "wrong_module"),
    (lambda s: s.update(format_version="99"), "unsupported_version"),
    (lambda s: s["resources"].update(history=[]), "unknown_section"),
    (lambda s: s["settings"].update(unknown=True), "unknown_setting"),
    (lambda s: s["settings"].update(default_speed=99), "invalid_setting"),
    (lambda s: s["resources"]["profiles"][0]["modules"]["opal-say"].update(voice_id="missing"), "voice_not_found"),
    (lambda s: s["resources"]["personas"][0].update(voice_id="missing"), "voice_not_found"),
    (lambda s: s["resources"]["profiles"].append(copy.deepcopy(s["resources"]["profiles"][0])), "duplicate_resource"),
    (lambda s: s["resources"].update(profiles={}), "schema_invalid"),
    (lambda s: s["resources"]["engines"][0]["options"].update(max_concurrent_inferences=0), "invalid_option"),
])
def test_invalid_candidate_never_changes_state(client, populated, mutation, code):
    before = state(client)
    candidate = copy.deepcopy(populated)
    mutation(candidate)
    response = client.post("/api/v1/config/validate", json={"snapshot": candidate})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["valid"] is False
    assert code in {error["code"] for error in report["errors"]}
    assert state(client) == before
    assert client.post("/api/v1/config/load", json={"snapshot": candidate}).status_code == 422
    assert state(client) == before


def test_validate_live_not_managed_and_no_runtime_effects(client, populated, app, monkeypatch):
    svc = app.state.services
    svc.config_ops.snapshot_path.write_text("{}", encoding="utf-8")
    before = state(client)
    runtime = [(id(engine), engine.options) for engine in svc.runtime.all()]

    def forbidden(*args, **kwargs):
        raise AssertionError("validation must not configure adapters")

    monkeypatch.setattr(svc.runtime, "prepare_configurations", forbidden)
    report = client.post("/api/v1/config/validate").json()
    assert report["valid"] is True, report
    assert [(id(engine), engine.options) for engine in svc.runtime.all()] == runtime
    assert svc.config_ops.snapshot_path.read_text(encoding="utf-8") == "{}"
    assert state(client) == before
    assert client.post("/api/v1/config/validate", json={"snapshot": {}}).json()["valid"] is False


def test_load_is_full_replacement_and_preserves_foreign_compartments(client, populated):
    populated["resources"]["profiles"][0]["modules"]["future-service"] = {"items": [1, 2], "nullable": None}
    response = client.post("/api/v1/config/load", json={"snapshot": populated})
    assert response.status_code == 200, response.text
    assert response.json()["warnings"][0]["code"] == "unknown_namespace"
    assert state(client)[0]["resources"]["profiles"][0]["modules"]["future-service"] == {"items": [1, 2], "nullable": None}
    empty = {"format_version": "1.0.0", "module": "opal-say", "created_at": populated["created_at"],
             "settings": {}, "resources": {}}
    assert client.post("/api/v1/config/load", json={"snapshot": empty}).status_code == 200
    assert client.get("/api/v1/voice/").json() == []
    assert client.get("/api/v1/profile/").json() == []
    assert client.get("/api/v1/persona/").json() == []
    assert client.get("/api/v1/setting/chunk_max_chars").json()["value"] == 400


def test_load_preconditions_and_missing_managed(client, populated):
    before = state(client)
    assert client.post("/api/v1/config/load").status_code == 404
    client.skip_if_match = True
    assert client.post("/api/v1/config/load", json={"snapshot": populated}).status_code == 428
    assert client.post("/api/v1/config/load", json={"snapshot": populated}, headers={"If-Match": '"stale"'}).status_code == 412
    assert state(client) == before


def test_failed_managed_save_keeps_previous_snapshot(client, populated, app, monkeypatch):
    assert client.post("/api/v1/config/save").status_code == 200
    path = app.state.services.config_ops.snapshot_path
    previous = path.read_bytes()
    before = state(client)

    def fail(*args):
        raise OSError("injected write failure")

    monkeypatch.setattr("opal_say.service.config_ops.os.replace", fail)
    assert client.post("/api/v1/config/save").status_code == 500
    assert path.read_bytes() == previous and state(client) == before
    assert not list(path.parent.glob(".snapshot-*.tmp"))


def test_failed_load_rolls_back_database_revision_and_runtime(client, populated, app, monkeypatch):
    before = state(client)
    runtime = [(id(engine), engine.options) for engine in app.state.services.runtime.all()]
    candidate = copy.deepcopy(populated)
    for engine in candidate["resources"]["engines"]:
        if engine["engine_id"] == "fake":
            engine["options"]["available"] = False
    original = Store._insert

    def fail(connection, table, row):
        if table == "personas":
            raise OSError("injected database failure")
        return original(connection, table, row)

    monkeypatch.setattr(Store, "_insert", staticmethod(fail))
    assert client.post("/api/v1/config/load", json={"snapshot": candidate}).status_code == 500
    assert state(client) == before
    assert [(id(engine), engine.options) for engine in app.state.services.runtime.all()] == runtime


def test_null_candidates_rejected_and_secrets_not_exported(client, populated):
    for operation in ("load", "validate"):
        assert client.post(f"/api/v1/config/{operation}", json={"snapshot": None}).status_code == 422
    for operation in ("save", "load", "validate"):
        assert client.post(f"/api/v1/config/{operation}", content="null",
                           headers={"Content-Type": "application/json"}).status_code == 422
    populated["resources"]["profiles"][0]["modules"]["future-service"] = {"token": "fixture-not-a-real-secret"}
    report = client.post("/api/v1/config/validate", json={"snapshot": populated}).json()
    assert report["valid"] is False
    assert report["errors"][0]["code"] == "secret_value"
    assert "fixture-not-a-real-secret" not in json.dumps(report)


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/backup/list"), ("POST", "/api/v1/backup/create"),
    ("GET", "/api/v1/backup/download/file.tar.gz"), ("POST", "/api/v1/backup/restore/file.tar.gz"),
    ("GET", "/api/v1/backup/export"), ("POST", "/api/v1/backup/import"),
    ("GET", "/api/v1/backup/validate"), ("POST", "/api/v1/backup/validate"),
    ("DELETE", "/api/v1/backup/file.tar.gz"),
])
def test_backup_interfaces_are_retired(client, method, path):
    client.skip_contract = True
    assert client.request(method, path).status_code == 404
