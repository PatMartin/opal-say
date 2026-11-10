"""ServiceProcessManager: pidfile tracking, kill, detached launch env, log tail,
wait-for-pid handshake. Only spawned sleepers are killed — never the suite itself."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from opal_say.config import ServiceConfig, load_config
from opal_say.service.errors import ApiError
from opal_say.service.lifecycle import (
    ServiceProcessManager,
    pid_alive,
    stop_pid,
    wait_for_pids,
    write_pid_file,
)


@pytest.fixture
def mgr(tmp_path: Path) -> ServiceProcessManager:
    return ServiceProcessManager(ServiceConfig(log_dir=tmp_path / "logs"))


@pytest.fixture
def sleeper():
    """A real child process that only sleeps; each test kills what it spawns."""
    procs: list[subprocess.Popen] = []

    def spawn():
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        procs.append(proc)
        return proc

    yield spawn
    for p in procs:
        if p.poll() is None:
            stop_pid(p.pid)


def test_pid_alive_self_and_dead(sleeper):
    assert pid_alive(os.getpid())
    proc = sleeper()
    proc.terminate()
    proc.wait(10)
    assert not pid_alive(proc.pid)


def test_stop_pid_kills_sleeper(mgr, sleeper):
    proc = sleeper()
    assert pid_alive(proc.pid)
    stop_pid(proc.pid)
    proc.wait(10)
    assert not pid_alive(proc.pid)


def test_tracked_pid_states(mgr, sleeper):
    assert mgr.tracked_pid() is None  # no pidfile
    mgr.pid_file.parent.mkdir(parents=True)
    mgr.pid_file.write_text("not-a-pid")
    assert mgr.tracked_pid() is None and not mgr.pid_file.exists()  # corrupt cleaned
    dead = sleeper()
    dead.terminate()
    dead.wait(10)
    mgr.pid_file.write_text(str(dead.pid))
    assert mgr.tracked_pid() is None and not mgr.pid_file.exists()  # stale cleaned
    live = sleeper()
    mgr.pid_file.write_text(str(live.pid))
    assert mgr.tracked_pid() == live.pid


def test_tail_log(mgr):
    assert mgr.tail_log(10) is None
    mgr.log_dir.mkdir(parents=True)
    mgr.log_file.write_text("\n".join(f"line{i}" for i in range(20)))
    result = mgr.tail_log(5)
    assert result["lines"] == [f"line{i}" for i in range(15, 20)]
    assert result["log_file"] == str(mgr.log_file)


def test_wait_for_pids(sleeper):
    done = sleeper()
    done.terminate()
    done.wait(10)
    wait_for_pids([done.pid], timeout=2)  # already dead: immediate
    with pytest.raises(TimeoutError):
        wait_for_pids([os.getpid()], timeout=0.5)


def test_launch_env_and_log_mode(mgr, monkeypatch):
    captured: dict = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    mgr.launch(append=False, wait_for_pids=[111, 222])
    assert captured["argv"] == [sys.executable, "-m", "opal_say"]
    env = captured["kwargs"]["env"]
    assert env["OPAL_SAY_PID_FILE"] == str(mgr.pid_file.resolve())
    assert env["OPAL_SAY_WAIT_FOR_PID"] == "111,222"
    assert env["OPAL_SAY_PORT"] == str(mgr.config.port)
    assert mgr.log_file.exists() and mgr.log_file.stat().st_size == 0

    mgr.log_file.write_text("old log\n")
    mgr.launch(append=True)
    assert mgr.log_file.read_text() == "old log\n"  # append preserves content


def test_launch_propagates_config_source(tmp_path, monkeypatch):
    cfg_file = tmp_path / "svc.yaml"
    cfg_file.write_text("service:\n  port: 8123\n")
    config = load_config(cfg_file)
    assert config.source_path == cfg_file.resolve()
    mgr = ServiceProcessManager(config)
    captured: dict = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["env"] = kwargs["env"]

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    mgr.launch()
    assert captured["env"]["OPAL_SAY_CONFIG"] == str(cfg_file.resolve())
    assert captured["env"]["OPAL_SAY_PORT"] == "8123"


def test_start_kills_tracked_other(mgr, sleeper, monkeypatch):
    tracked = sleeper()
    mgr.pid_file.parent.mkdir(parents=True)
    mgr.pid_file.write_text(str(tracked.pid))
    monkeypatch.setattr(mgr, "launch", lambda **kw: sleeper())
    status, terminate_self = mgr.start()
    tracked.wait(10)
    assert not pid_alive(tracked.pid)
    assert status["previous_pid"] == tracked.pid
    assert status["state"] == "running"
    assert not terminate_self


def test_start_self_restart_flags_termination(mgr, monkeypatch):
    class DummyProc:
        pid = 4242

        def poll(self):
            return None

    mgr.pid_file.parent.mkdir(parents=True)
    mgr.pid_file.write_text(str(os.getpid()))
    waits: list = []
    monkeypatch.setattr(
        mgr, "launch",
        lambda append=False, wait_for_pids=(): waits.extend(wait_for_pids) or DummyProc(),
    )
    status, terminate_self = mgr.start(append=True)
    assert terminate_self
    assert waits == [os.getpid()]
    assert status["previous_pid"] == os.getpid()
    assert status["pid"] == 4242


def test_start_reports_early_child_death(mgr, monkeypatch):
    class DeadOnArrival:
        pid = 1

        def poll(self):
            return 1

    monkeypatch.setattr(mgr, "launch", lambda **kw: DeadOnArrival())
    with pytest.raises(ApiError) as err:
        mgr.start()
    assert err.value.status == 500


def test_stop_variants(mgr, sleeper):
    status, terminate_self = mgr.stop()
    assert status["state"] == "stopped" and status["previous_pid"] is None
    assert not terminate_self

    tracked = sleeper()
    mgr.pid_file.parent.mkdir(parents=True)
    mgr.pid_file.write_text(str(tracked.pid))
    status, terminate_self = mgr.stop()
    tracked.wait(10)
    assert not pid_alive(tracked.pid)
    assert status["previous_pid"] == tracked.pid
    assert not terminate_self
    assert not mgr.pid_file.exists()


def test_stop_self_flags_termination(mgr):
    mgr.pid_file.parent.mkdir(parents=True)
    mgr.pid_file.write_text(str(os.getpid()))
    status, terminate_self = mgr.stop()
    assert terminate_self
    assert status["previous_pid"] == os.getpid()


def test_write_pid_file(tmp_path):
    target = tmp_path / "nested" / "opal_say.pid"
    write_pid_file(target)
    assert target.read_text().strip() == str(os.getpid())
