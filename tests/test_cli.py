"""CLI tests: commands run against the real REST app (in-process) through the HTTP client."""

from __future__ import annotations

import json
import shlex
import sys
import wave

import pytest
import typer
from contract_check import check_response, match_operation
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from opal_say.cli import audio
from opal_say.cli import main as cli
from opal_say.cli.client import ApiClient
from opal_say.contract import contract_operations


class RecordingTestClient(TestClient):
    def __init__(self, app):
        super().__init__(app)
        self.ops: list[str] = []

    def send(self, request, *args, **kwargs):  # type: ignore[override]
        resp = super().send(request, *args, **kwargs)
        op = match_operation(request.method, request.url.path)
        if op:
            self.ops.append(op["operationId"])
            if not kwargs.get("stream"):
                check_response(resp)
        return resp


@pytest.fixture
def http(app, monkeypatch):
    with RecordingTestClient(app) as tc:
        monkeypatch.setattr(cli, "client_factory", lambda url, timeout: ApiClient(url, timeout, http=tc))
        yield tc


@pytest.fixture
def run(http):
    runner = CliRunner()

    def invoke(args: str, code: int = 0, stdin: str | None = None):
        result = runner.invoke(cli.app, ["say", *shlex.split(args)], input=stdin)
        assert result.exit_code == code, f"{args}\nstdout: {result.stdout}\nstderr: {result.stderr}"
        return result

    return invoke


def test_full_cli_workflow(run, http, tmp_path, config):
    out = run("--json engine list").stdout
    assert [e["engine_id"] for e in json.loads(out)] == ["alt", "fake"]
    assert "fake" in run("engine list").stdout
    assert json.loads(run("--json engine get fake").stdout)["engine_id"] == "fake"
    assert json.loads(run("engine update alt --disable -O available=true").stdout)["enabled"] is False
    assert "valid" in run("engine validate fake").stdout

    run('voice add v1 --name "Voice One" --engine fake --ref tone-a -O pitch=300 --language en-US')
    run("voice add v2 --name Two --engine fake --ref tone-b --disabled")
    listing = json.loads(run("--json voice list --engine fake --enabled").stdout)
    assert [v["voice_id"] for v in listing] == ["v1"]
    assert json.loads(run("voice get v1").stdout)["engine_options"] == {"pitch": 300}
    assert json.loads(run("voice update v2 --enabled --description hi").stdout)["description"] == "hi"
    assert json.loads(run("voice update v2 --clear-description").stdout)["description"] is None
    run("voice validate v1")
    wav = tmp_path / "test.wav"
    result = run(f"voice test v1 -o {wav.as_posix()} --text Hello --sample-rate 16000")
    with wave.open(str(wav)) as w:
        assert w.getframerate() == 16000
    assert "voice=v1" in result.stderr

    p = json.loads(run("profile add main --voice v1 --active --speed 1.5 --format pcm").stdout)
    assert p["is_active"] and p["output_format"] == "pcm"
    p2 = json.loads(run("profile add other --voice v2 --options-json '{\"pitch\": 600}'").stdout)
    assert "main" in run("profile list").stdout
    assert json.loads(run(f"profile get {p['id']}").stdout)["speed"] == 1.5
    assert json.loads(run(f"profile update {p2['id']} --volume 0.5 --native-rate").stdout)["volume"] == 0.5
    assert json.loads(run(f"profile activate {p2['id']}").stdout)["is_active"] is True
    run(f"profile remove {p['id']}")

    run('persona add amy --name "Amy" --voice v1')
    assert "amy" in run("persona list").stdout
    assert json.loads(run("persona get amy").stdout)["voice_id"] == "v1"
    assert json.loads(run("persona update amy --description warm").stdout)["description"] == "warm"
    persona_wav = tmp_path / "persona.wav"
    run(f'generate "as Amy" --persona amy -o {persona_wav.as_posix()}')
    assert persona_wav.stat().st_size > 0
    run("persona remove amy")

    pcm = tmp_path / "out.pcm"
    result = run(f'generate "Hello there. General Kenobi." -o {pcm.as_posix()} --format pcm --sample-rate 8000')
    assert pcm.stat().st_size > 0 and "8000 Hz" in result.stderr
    streamed = tmp_path / "stream.pcm"
    run(f"generate - --stream -o {streamed.as_posix()} --voice v1", stdin="Streaming via stdin.")
    assert streamed.stat().st_size > 0
    stdout_audio = run("generate Hi --voice v1 -o -").stdout_bytes
    assert stdout_audio[:4] == b"RIFF"

    run("setting set chunk_max_chars 200")
    assert json.loads(run("setting list").stdout) == {"chunk_max_chars": 200}
    assert json.loads(run("setting get chunk_max_chars").stdout)["value"] == 200
    run("setting delete chunk_max_chars")
    assert json.loads(run("setting patch --json '{\"greeting\": \"hi\"}'").stdout)["greeting"] == "hi"
    run("setting patch --json '{\"greeting\": null}'")

    assert "status:" in run("info health").stdout
    assert json.loads(run("--json info live").stdout)["ready"] is True
    assert json.loads(run("--json info ready").stdout)["ready"] is True
    assert json.loads(run("info version").stdout)["name"] == "opal-say-svc"
    assert run("info ping").stdout.strip() == "ok"

    filename = json.loads(run("backup create").stdout)["filename"]
    assert filename in run("backup list").stdout
    target = tmp_path / "b.tar.gz"
    run(f"backup download {filename} -o {target.as_posix()}")
    assert target.stat().st_size > 0
    assert json.loads(run(f"backup restore {filename}").stdout)["success"] is True
    export = tmp_path / "export.json"
    run(f"backup export -o {export.as_posix()}")
    assert json.loads(export.read_text())["voices"][0]["voice_id"] == "v1"
    run("backup validate")
    run(f"backup validate --payload {export.as_posix()}")
    config.config_dir.mkdir(parents=True, exist_ok=True)
    (config.config_dir / "settings.yaml").write_text("settings:\n  default_volume: 0.9\n")
    assert json.loads(run("backup import").stdout)["success"] is True
    run(f"backup delete {filename}")
    run("voice remove v1")
    result = run("voice remove v2", code=1)  # still referenced by profile 'other'
    assert "voice_in_use" in result.stderr


def test_every_operation_is_reachable_from_cli(run, http, tmp_path, config):
    """Parity: each contract operation is invoked by its mapped CLI command."""
    test_full_cli_workflow(run, http, tmp_path, config)
    missing = set(contract_operations()) - set(http.ops)
    assert not missing, f"CLI never called: {sorted(missing)}"


def test_error_reporting(run):
    result = run("voice get ghost", code=1)
    assert "HTTP 404 voice_not_found" in result.stderr
    result = run("voice add bad --name x --engine fake --ref nope", code=1)
    assert "validation error" in result.stderr and "engine_voice_ref" in result.stderr
    result = run('generate "x" -o /dev/null', code=1)
    assert "no_voice" in result.stderr
    result = run('generate "x" --stream -o /dev/null', code=1)
    assert "409 no_voice" in result.stderr
    run("engine update fake", code=2)
    run("generate x --stream --format wav -o -", code=2)
    run("voice add a --name a --engine fake --ref tone-a -O novalue", code=2)


def test_invalid_validation_result_exits_nonzero(run):
    run("voice add v --name V --engine fake --ref tone-a")
    run("engine update fake -O available=false")
    result = run("voice validate v", code=1)
    assert "INVALID" in result.stdout and "engine_unavailable" in result.stdout


def test_unreachable_server(monkeypatch):
    monkeypatch.setattr(cli, "client_factory", lambda url, timeout: ApiClient("http://127.0.0.1:9", 1))
    result = CliRunner().invoke(cli.app, ["say", "info", "ping"])
    assert result.exit_code == 2 and "cannot reach" in result.stderr


def test_tree_command_prints_command_surface():
    result = CliRunner().invoke(cli.app, ["say", "tree"])
    assert result.exit_code == 0
    out = result.stdout
    for cmd in ("generate", "engine", "voice", "profile", "setting", "backup", "health"):
        assert cmd in out
    assert "speak" not in out  # hidden default command
    # Required args/options are bare; optional ones are bracketed.
    assert "generate TEXT [-o/--output" in out
    assert "get ENGINE_ID" in out
    assert "set KEY VALUE" in out
    assert "[--stream]" in out and "[--enable/--disable]" in out
    assert "│   ├──" in out and "└──" in out


def test_generate_plays_when_asked(run, monkeypatch):
    played: list[int] = []
    monkeypatch.setattr(audio, "play_response", lambda resp: played.append(len(resp.content)) or len(resp.content))
    run('voice add v1 --name "Voice One" --engine fake --ref tone-a')
    run('generate "Hello there." --voice v1 --play')
    assert played and played[0] > 0


def test_generate_writes_and_plays(run, monkeypatch, tmp_path):
    played: list[int] = []
    monkeypatch.setattr(audio, "play_response", lambda resp: played.append(len(resp.content)) or len(resp.content))
    run('voice add v1 --name "Voice One" --engine fake --ref tone-a')
    wav = tmp_path / "out.wav"
    run(f'generate "Save and speak." --voice v1 --play -o {wav.as_posix()}')
    assert wav.read_bytes()[:4] == b"RIFF" and played == [wav.stat().st_size]


def test_generate_writes_only_without_play(run, monkeypatch, tmp_path):
    monkeypatch.setattr(audio, "play_response", lambda resp: pytest.fail("playback must not run"))
    run('voice add v1 --name "Voice One" --engine fake --ref tone-a')
    wav = tmp_path / "quiet.wav"
    run(f'generate "Quiet export." --voice v1 -o {wav.as_posix()}')
    assert wav.read_bytes()[:4] == b"RIFF"


def test_generate_stdout_disables_playback(run, monkeypatch):
    monkeypatch.setattr(audio, "play_response", lambda resp: pytest.fail("playback must not run"))
    run('voice add v1 --name "Voice One" --engine fake --ref tone-a')
    result = run('generate "Pipe me." --voice v1 -o -')
    assert result.stdout_bytes[:4] == b"RIFF"


def test_generate_requires_a_destination(run):
    run('voice add v1 --name "Voice One" --engine fake --ref tone-a')
    result = run('generate "No destination." --voice v1', code=2)
    assert "supply -o/--output" in result.stderr


def test_generate_stream_plays_progressively(run, monkeypatch):
    chunks: list[bytes] = []
    rates: list[int] = []

    def fake_stream(iterable, rate, **kw):
        rates.append(rate)
        data = list(iterable)
        chunks.extend(data)
        return sum(len(c) for c in data)

    monkeypatch.setattr(audio, "play_pcm_stream", fake_stream)
    run('voice add v1 --name "Voice One" --engine fake --ref tone-a')
    run('generate "Streaming speech." --voice v1 --stream --play')
    assert chunks and rates == [rates[0]] * len(rates)


def test_say_text_stream_writes_and_plays(run, monkeypatch, tmp_path):
    chunks: list[bytes] = []

    def fake_stream(iterable, rate, **kw):
        data = list(iterable)
        chunks.extend(data)
        return sum(len(c) for c in data)

    monkeypatch.setattr(audio, "play_pcm_stream", fake_stream)
    run('voice add v1 --name "Voice One" --engine fake --ref tone-a')
    pcm = tmp_path / "out.pcm"
    run(f'generate "Stream and save." --voice v1 --stream --play -o {pcm.as_posix()}')
    assert chunks and pcm.stat().st_size == sum(len(c) for c in chunks)


def test_generate_reads_stdin(run, monkeypatch):
    monkeypatch.setattr(audio, "play_response", lambda resp: len(resp.content))
    run('voice add v1 --name "Voice One" --engine fake --ref tone-a')
    run("generate - --voice v1 --play", stdin="stdin speech")


def test_subcommands_still_win_over_text(run):
    run('voice add v1 --name "Voice One" --engine fake --ref tone-a')
    assert "v1" in run("voice list").stdout  # 'voice' resolves as a command, not text


def test_generate_and_voice_test_play(run, monkeypatch, tmp_path):
    played: list[int] = []
    monkeypatch.setattr(audio, "play_response", lambda resp: played.append(len(resp.content)) or len(resp.content))
    run('voice add v1 --name "Voice One" --engine fake --ref tone-a')
    wav = tmp_path / "g.wav"
    run(f'generate "played generate" --voice v1 --play -o {wav.as_posix()}')
    run('voice test v1 --play --text "played test"')
    assert len(played) == 2


def test_generate_play_and_stdout_conflict(run):
    run('voice add v1 --name "Voice One" --engine fake --ref tone-a')
    result = run('generate "x" --voice v1 --play -o -', code=2)
    assert "stdout" in result.stderr


def test_playback_requires_simpleaudio(monkeypatch):
    monkeypatch.setitem(sys.modules, "simpleaudio", None)  # simulate the extra not being installed
    with pytest.raises(typer.BadParameter, match=r"opal-say\[audio\]"):
        audio._simpleaudio()


def test_worker_crash_is_a_clean_error(monkeypatch):
    """A worker that dies (even natively) must not crash the CLI process."""
    monkeypatch.setattr(audio, "_worker_args", lambda *a: [sys.executable, "-c", "import sys; sys.exit(1)"])
    with pytest.raises(typer.BadParameter, match="playback failed"):
        audio.play_pcm(b"\x00" * 100, 24000)
