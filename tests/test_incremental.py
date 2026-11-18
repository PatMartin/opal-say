"""Incremental text input for /api/v1/generate/stream/incremental (NDJSON duplex)."""

from __future__ import annotations

import http.client
import json
import time

import pytest

from conftest import make_voice

SENTENCES = " ".join(f"This is sentence number {i}." for i in range(6))


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def voices(client):
    make_voice(client, "v1", ref="tone-a")
    make_voice(client, "v2", ref="tone-b")
    client.put("/api/v1/setting/chunk_max_chars", json={"value": "50"})


def _lines(*records) -> bytes:
    return "".join(json.dumps(r) + "\n" for r in records).encode()


def test_incremental_stream_matches_one_shot(client, voices):
    # Boundary-free text chunks identically in both modes -> byte-identical PCM.
    body = _lines({"voice_id": "v1"}, {"text": "Hello there."}, {"done": True})
    inc = client.post(
        "/api/v1/generate/stream/incremental", content=body,
        headers={"content-type": "application/x-ndjson"},
    )
    assert inc.status_code == 200
    assert inc.headers["x-opal-audio-format"] == "pcm"
    one_shot = client.post("/api/v1/generate/stream", json={"text": "Hello there.", "voice_id": "v1"})
    assert one_shot.status_code == 200
    assert inc.content == one_shot.content


def test_eof_finishes_input_without_done(client, voices):
    body = _lines({"voice_id": "v1"}, {"text": "First sentence."}, {"text": "Second sentence."})
    resp = client.post(
        "/api/v1/generate/stream/incremental", content=body,
        headers={"content-type": "application/x-ndjson"},
    )
    assert resp.status_code == 200 and len(resp.content) > 0


def test_trailing_fragment_without_sentence_boundary_is_flushed(client, voices, engines):
    body = _lines({"voice_id": "v1"}, {"text": "unterminated fragment"}, {"done": True})
    resp = client.post(
        "/api/v1/generate/stream/incremental", content=body,
        headers={"content-type": "application/x-ndjson"},
    )
    assert resp.status_code == 200 and len(resp.content) > 0
    assert engines[0].calls[-1]["text"] == "unterminated fragment"


def test_split_sentences_synthesize_in_order(client, voices, engines):
    long_sentences = [
        "Sentence number one is done here.",
        "Sentence number two follows now.",
        "And the third one ends it all.",
    ]
    body = _lines(
        {"voice_id": "v1"},
        {"text": " ".join(long_sentences)},
        {"done": True},
    )
    resp = client.post(
        "/api/v1/generate/stream/incremental", content=body,
        headers={"content-type": "application/x-ndjson"},
    )
    assert resp.status_code == 200
    sent = [c["text"] for c in engines[0].calls[-3:]]
    assert sent == long_sentences


def test_params_line_validation_errors(client, voices):
    # Missing params line.
    r = client.post(
        "/api/v1/generate/stream/incremental", content=b"",
        headers={"content-type": "application/x-ndjson"},
    )
    assert r.status_code == 422
    # Params line is not JSON.
    r = client.post(
        "/api/v1/generate/stream/incremental", content=b"nope\n",
        headers={"content-type": "application/x-ndjson"},
    )
    assert r.status_code == 422
    # Params violate field constraints.
    r = client.post(
        "/api/v1/generate/stream/incremental",
        content=_lines({"voice_id": "v1", "speed": 99.0}),
        headers={"content-type": "application/x-ndjson"},
    )
    assert r.status_code == 422
    # Unknown voice resolves like a normal generation failure (409).
    r = client.post(
        "/api/v1/generate/stream/incremental",
        content=_lines({"voice_id": "ghost"}),
        headers={"content-type": "application/x-ndjson"},
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "voice_not_found"


def test_malformed_record_returns_json_error(client, voices):
    body = b'{"voice_id": "v1"}\nnot json\n'
    resp = client.post(
        "/api/v1/generate/stream/incremental", content=body,
        headers={"content-type": "application/x-ndjson"},
    )
    assert resp.status_code == 422


def test_incremental_respects_max_text_chars(client, voices):
    assert client.put("/api/v1/setting/max_text_chars", json={"value": 50}).status_code == 200
    body = _lines({"voice_id": "v1"}, {"text": "x" * 60})
    resp = client.post(
        "/api/v1/generate/stream/incremental", content=body,
        headers={"content-type": "application/x-ndjson"},
    )
    assert resp.status_code == 413


def _chunked(data: bytes) -> bytes:
    return f"{len(data):x}\r\n".encode() + data + b"\r\n"


def _duplex_conn(live_server: str) -> http.client.HTTPConnection:
    host, port = live_server.replace("http://", "").rsplit(":", 1)
    conn = http.client.HTTPConnection(host, int(port), timeout=15)
    conn.putrequest("POST", "/api/v1/generate/stream/incremental")
    conn.putheader("Content-Type", "application/x-ndjson")
    conn.putheader("Transfer-Encoding", "chunked")
    conn.endheaders()
    return conn


def test_first_audio_arrives_before_producer_finishes(live_server, client, voices):
    """True duplex: the client reads synthesized audio while its own request
    body is still open — a conversation producer never has to buffer text."""
    conn = _duplex_conn(live_server)
    try:
        conn.send(_chunked(_lines({"voice_id": "v1"})))
        conn.send(_chunked(_lines({"text": "Sentence number one.", "flush": True})))
        resp = conn.getresponse()
        assert resp.status == 200
        first = resp.read1(1 << 16)
        assert first  # audio arrived while the request body was still open
        # Producer keeps supplying text after first audio already flowed.
        conn.send(_chunked(_lines({"text": "Sentence number two.", "flush": True})))
        conn.send(_chunked(_lines({"done": True})))
        conn.send(_chunked(b""))
        assert resp.read1(1 << 20)
    finally:
        conn.close()


def test_slow_producer_is_synthesized_in_order(live_server, client, voices, engines):
    segments = [
        "First segment arrives slowly but completely.",
        "Second segment follows after a pause in input.",
        "Third segment ends the increment cleanly here.",
    ]
    conn = _duplex_conn(live_server)
    try:
        conn.send(_chunked(_lines({"voice_id": "v1"})))
        for seg in segments:
            conn.send(_chunked(_lines({"text": seg, "flush": True})))
            time.sleep(0.3)
        conn.send(_chunked(_lines({"done": True})))
        conn.send(_chunked(b""))
        resp = conn.getresponse()
        assert resp.status == 200
        assert resp.read()
    finally:
        conn.close()
    sent = [c["text"] for c in engines[0].calls[-3:]]
    assert sent == segments


def test_disconnect_mid_increment_frees_engine(live_server, client, voices, app):
    """Closing the connection while input is unfinished aborts generation and
    releases the runtime reference and inference permit."""
    rec = app.state.services.runtime._records["fake"]
    conn = _duplex_conn(live_server)
    conn.send(_chunked(_lines({"voice_id": "v1"})))
    conn.send(_chunked(_lines({"text": "Sentence number one.", "flush": True})))
    resp = conn.getresponse()
    assert resp.status == 200
    assert resp.read1(1 << 16)
    conn.close()  # body left incomplete — interrupted input
    assert _wait_until(lambda: rec.refs == 0, timeout=10)
    assert client.post("/api/v1/generate", json={"text": "ok", "voice_id": "v1"}).status_code == 200
