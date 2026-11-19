"""Readiness: /api/v1/health/ready reflects the default speech path, not just engine liveness.

The probe resolves what a bare generation request would use (active profile -> voice ->
engine -> voice assets) and fails 503 with an actionable `details.speech` record when any
stage is broken. Engine states stay visible independently under `engines`.
"""

from __future__ import annotations

from conftest import make_profile, make_voice


def _ready(client):
    resp = client.get("/api/v1/health/ready")
    return resp.status_code, resp.json()


def test_zero_voices_is_unavailable(client):
    status, body = _ready(client)
    assert status == 503
    assert body["ready"] is False and body["status"] == "unavailable"
    speech = body["details"]["speech"]
    assert speech["ok"] is False and speech["code"] == "no_voice"
    # Engine states remain independently visible even while the service is unready.
    assert {e["engine_id"]: e["available"] for e in body["details"]["engines"]} == {"alt": True, "fake": True}


def test_no_active_profile_is_unavailable(client):
    make_voice(client, "v1")
    make_profile(client, "p1", "v1")  # registered but not active
    status, body = _ready(client)
    assert status == 503 and body["details"]["speech"]["code"] == "no_voice"


def test_healthy_default_path_is_ready(client):
    p = make_profile(client, "p1", make_voice(client, "v1")["voice_id"], is_active=True)
    status, body = _ready(client)
    assert status == 200
    assert body["ready"] is True and body["status"] == "ok"
    speech = body["details"]["speech"]
    assert speech["ok"] is True
    assert speech["voice_id"] == "v1" and speech["profile_id"] == p["id"] and speech["engine_id"] == "fake"
    assert speech["native_sample_rate"] == 22050


def test_default_voice_missing(client, app):
    make_profile(client, "p1", make_voice(client, "v1")["voice_id"], is_active=True)
    app.state.services.store.delete_voice("v1")  # bypasses the in-use guard
    status, body = _ready(client)
    assert status == 503
    speech = body["details"]["speech"]
    assert speech["code"] == "voice_not_found" and speech["voice_id"] == "v1"


def test_default_voice_disabled(client, app):
    make_profile(client, "p1", make_voice(client, "v1")["voice_id"], is_active=True)
    app.state.services.store.update_voice("v1", {"enabled": False})
    status, body = _ready(client)
    assert status == 503 and body["details"]["speech"]["code"] == "voice_disabled"


def test_default_voice_engine_unavailable(client):
    make_profile(client, "p1", make_voice(client, "v1")["voice_id"], is_active=True)
    assert client.patch("/api/v1/engine/fake", json={"options": {"available": False}}).status_code == 200
    status, body = _ready(client)
    assert status == 503
    speech = body["details"]["speech"]
    assert speech["code"] == "engine_unavailable" and speech["engine_id"] == "fake"


def test_default_voice_asset_unresolvable(client, app):
    make_profile(client, "p1", make_voice(client, "v1")["voice_id"], is_active=True)
    app.state.services.store.update_voice("v1", {"engine_voice_ref": "ghost"})
    status, body = _ready(client)
    assert status == 503 and body["details"]["speech"]["code"] == "model_not_found"


def test_default_voice_options_invalid(client, app):
    make_profile(client, "p1", make_voice(client, "v1")["voice_id"], is_active=True)
    app.state.services.store.update_voice("v1", {"engine_options": {"pitch": "loud"}})
    status, body = _ready(client)
    assert status == 503 and body["details"]["speech"]["code"] == "invalid_options"


def test_usable_alternative_does_not_hide_broken_default(client, app):
    """A healthy explicitly-selectable voice cannot make a broken default path ready:
    readiness reports the default failure (with enabled_voices showing the alternative)."""
    make_profile(client, "p1", make_voice(client, "v1")["voice_id"], is_active=True)
    make_voice(client, "v2", ref="tone-b")
    app.state.services.store.update_voice("v1", {"enabled": False})
    status, body = _ready(client)
    assert status == 503
    speech = body["details"]["speech"]
    assert speech["code"] == "voice_disabled" and speech["voice_id"] == "v1"
    assert body["details"]["enabled_voices"] == 1  # v2 stays usable for explicit selection


def test_degraded_engine_stays_ready_when_default_path_works(client):
    make_profile(client, "p1", make_voice(client, "v1")["voice_id"], is_active=True)
    assert client.patch("/api/v1/engine/alt", json={"options": {"available": False}}).status_code == 200
    status, body = _ready(client)
    assert status == 200 and body["ready"] is True and body["status"] == "degraded"


def test_readiness_recovers_after_default_fixed(client):
    make_voice(client, "v1")
    assert _ready(client)[0] == 503
    make_profile(client, "p1", "v1", is_active=True)
    status, body = _ready(client)
    assert status == 200 and body["ready"] is True
