"""CLI playback sink: bounded continuous playback, ordering, underruns, stop."""

from __future__ import annotations

import io
import sys
import threading
import time
import types

import pytest

from opal_say.cli import audio


class _FakePlay:
    """PlayObject stand-in: 'playing' until the block's virtual end time."""

    def __init__(self, sa: _FakeSA, block: bytes):
        self._sa = sa
        self.end = self._sa._clock() + len(block) * sa.sec_per_byte

    def is_playing(self) -> bool:
        return self._sa._clock() < self.end

    def stop(self) -> None:
        self.end = 0


class _FakeSA:
    """simpleaudio stand-in: records play order; each block 'sounds' for
    len(block)*sec_per_byte seconds of virtual time."""

    def __init__(self, sec_per_byte: float = 0.0):
        self.sec_per_byte = sec_per_byte
        self.order: list[bytes] = []
        self.plays: list[_FakePlay] = []
        self.stops = 0

    def _clock(self) -> float:
        return time.monotonic()

    def play_buffer(self, block, num_channels, bytes_per_sample, sample_rate):
        play = _FakePlay(self, block)
        self.order.append(block)
        self.plays.append(play)
        return play

    def stop_all(self):
        self.stops += 1
        for play in self.plays:
            play.end = 0

    def finish_oldest(self, n: int = 1) -> None:
        """Force the n oldest outstanding blocks to complete (test control)."""
        for play in self.plays[:n]:
            play.end = 0
        del self.plays[:n]


def _feed(monkeypatch, sa: _FakeSA, data: bytes, align: int = 2, max_bytes: int = 1 << 20,
          frame_bytes: int = 32 << 10) -> int:
    monkeypatch.setattr(audio, "_simpleaudio", lambda: sa)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(data)))
    return audio._play_simpleaudio(align, 22050, 1, 2, max_buffer_bytes=max_bytes,
                                   frame_bytes=frame_bytes)


def _spy_sink(monkeypatch) -> dict:
    holder = {}

    class _Spy(audio._AudioSink):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            holder["sink"] = self

    monkeypatch.setattr(audio, "_AudioSink", _Spy)
    return holder


def test_blocks_play_in_order_and_drain(monkeypatch):
    sa = _FakeSA(sec_per_byte=0.001)  # 2-byte blocks finish in ~2ms each
    blocks = [f"block{i}".encode() * 2 for i in range(4)]
    assert _feed(monkeypatch, sa, b"".join(blocks), align=2, frame_bytes=12) == 0
    assert sa.order == blocks            # order preserved
    assert all(p.end <= time.monotonic() for p in sa.plays)  # drain waited for the queue
    assert sa.stops == 0                 # clean EOF is not an explicit stop


def test_trailing_partial_frame_is_truncation_error(monkeypatch):
    sa = _FakeSA()
    with pytest.raises(RuntimeError, match="trailing bytes"):
        _feed(monkeypatch, sa, b"odd", align=2)  # 3 bytes, frame alignment 2
    # the last complete frame still played before the error was reported
    assert sa.order == [b"od"]


def test_underrun_counted_across_slow_reads(monkeypatch):
    sa = _FakeSA(sec_per_byte=0)  # hardware instantly catches up with the producer
    holder = _spy_sink(monkeypatch)

    class _SlowStdin:
        def read1(self, _n):
            if not hasattr(self, "_it"):
                self._it = iter([b"aa", b"bb", b"cc"])
            return next(self._it, b"")

    monkeypatch.setattr(audio, "_simpleaudio", lambda: sa)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=_SlowStdin()))
    assert audio._play_simpleaudio(2, 22050, 1, 2, max_buffer_bytes=1 << 20) == 0
    assert sa.order == [b"aa", b"bb", b"cc"]
    assert holder["sink"].underruns == 2  # blocks 2 and 3 each found a starved queue


def test_buffer_bound_backpressures_the_feeder(monkeypatch):
    sa = _FakeSA(sec_per_byte=3600)  # nothing finishes until the test releases it
    data = b"a" * 64 + b"b" * 64 + b"c" * 64
    done = threading.Event()

    def run():
        _feed(monkeypatch, sa, data, align=2, max_bytes=64)
        done.set()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout=3)
    assert not done.is_set()          # feeder parked: 192 bytes > 64-byte buffer
    assert sa.order == [b"a" * 64]    # only the first block reached the backend
    sa.finish_oldest(1)
    deadline = time.monotonic() + 5
    while not done.is_set() and time.monotonic() < deadline:
        sa.finish_oldest(len(sa.plays))
        time.sleep(0.01)
    assert done.is_set()
    assert sa.order == [b"a" * 64, b"b" * 64, b"c" * 64]
    t.join(timeout=3)


def test_interrupt_stops_playback_explicitly(monkeypatch):
    sa = _FakeSA(sec_per_byte=3600)
    holder = _spy_sink(monkeypatch)

    class _InterruptStdin:
        def read1(self, _n):
            if not hasattr(self, "sent"):
                self.sent = True
                return b"aa"
            raise KeyboardInterrupt

    monkeypatch.setattr(audio, "_simpleaudio", lambda: sa)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(buffer=_InterruptStdin()))
    code = audio._play_simpleaudio(2, 22050, 1, 2)
    assert code == 130            # interrupted exit status
    assert sa.stops == 1          # explicit stop reached the backend
    assert all(not p.is_playing() for p in sa.plays)  # queued audio discarded, not drained
    assert holder["sink"]._pending_bytes == 0


def test_sink_reaps_in_order_and_bounds_bytes():
    sa = _FakeSA(sec_per_byte=3600)
    sink = audio._AudioSink(lambda b: sa.play_buffer(b, 1, 2, 22050), sa.stop_all, max_bytes=4)
    sink.enqueue(b"aa")
    sink.enqueue(b"bb")           # buffer full at 4 bytes
    sa.finish_oldest(1)
    released = threading.Event()

    def third():
        sink.enqueue(b"cc")
        released.set()

    t = threading.Thread(target=third, daemon=True)
    t.start()
    t.join(timeout=2)
    assert released.is_set()      # one finished block freed room
    assert sa.order == [b"aa", b"bb", b"cc"]
    sink.stop()
    assert sa.stops == 1
