"""`/api/v1/service/*` routes: managed server lifecycle over REST.

The app fixture injects FakeLifecycle, so these exercise routing, request/response
contract shape and the self-termination handshake — never real processes.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def lifecycle(app):
    return app.state.services.lifecycle


def test_service_start(client, lifecycle):
    resp = client.post("/api/v1/service/start", json={"append": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "running" and body["pid"] and body["log_file"]
    assert lifecycle.starts == [True]
    assert not lifecycle.terminated


def test_service_start_body_optional(client, lifecycle):
    assert client.post("/api/v1/service/start").status_code == 200
    assert lifecycle.starts == [False]


def test_service_start_restarting_self_schedules_termination(client, lifecycle):
    lifecycle.self_managed = True
    resp = client.post("/api/v1/service/start")
    assert resp.status_code == 200
    assert resp.json()["previous_pid"] is not None
    assert lifecycle.terminated  # BackgroundTask ran after the response was sent


def test_service_stop(client, lifecycle):
    resp = client.post("/api/v1/service/stop")
    assert resp.status_code == 200
    assert resp.json()["state"] == "stopped"
    assert lifecycle.stops == 1
    assert not lifecycle.terminated


def test_service_stop_stopping_self_schedules_termination(client, lifecycle):
    lifecycle.self_managed = True
    assert client.post("/api/v1/service/stop").status_code == 200
    assert lifecycle.terminated


def test_service_log(client, lifecycle):
    body = client.get("/api/v1/service/log?lines=1").json()
    assert body["lines"] == ["ready"]
    assert client.get("/api/v1/service/log").json()["lines"] == ["boot", "ready"]
    assert client.get("/api/v1/service/log?lines=0").status_code == 422
    assert client.get("/api/v1/service/log?lines=1001").status_code == 422


def test_service_log_missing_is_404(client, lifecycle):
    lifecycle.lines = None
    assert client.get("/api/v1/service/log").status_code == 404


def test_service_log_rejects_noninteger_lines(client):
    assert client.get("/api/v1/service/log?lines=abc").status_code == 422
