"""Local audio playback helpers for the CLI.

The REST service never plays audio. This optional client-side layer plays it only when
the user asks (`opal say TEXT`, `--play`).

Playback runs in a child worker process (`python -m opal_say.cli.audio`): audio output is
inherently stateful, and an unusable simpleaudio build can crash natively — isolating it
turns a segfault into a clean "playback failed" error instead of taking down the CLI.
The worker reads raw PCM on stdin, which also enables progressive playback while the
service is still synthesizing. On Windows a worker failure falls back to the stdlib
`winsound` module, which needs no external dependency.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import wave
from collections.abc import Iterable

import httpx
import typer


def _simpleaudio():
    try:
        import simpleaudio  # type: ignore[import-not-found]
    except ImportError:
        raise typer.BadParameter(
            "local playback requires simpleaudio: pip install 'opal-say[audio]'",
            param_hint="--play/--no-play",
        ) from None
    return simpleaudio


def _worker_args(sample_rate: int, channels: int, bytes_per_sample: int, backend: str) -> list[str]:
    return [sys.executable, "-m", "opal_say.cli.audio", "--backend", backend,
            "--rate", str(sample_rate), "--channels", str(channels), "--bytes-per-sample",
            str(bytes_per_sample)]


def _run_worker(args: list[str], chunks: Iterable[bytes], sent: list[bytes]) -> tuple[int, str]:
    """Feed chunks to a playback worker. Returns (exit_code, stderr)."""
    try:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        return -1, f"could not start the audio worker: {exc}"
    try:
        assert proc.stdin is not None
        for chunk in chunks:
            sent.append(chunk)
            proc.stdin.write(chunk)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        if proc.stdin is not None:
            with contextlib.suppress(OSError):
                proc.stdin.close()
    err = proc.stderr.read().decode(errors="replace").strip() if proc.stderr else ""
    return proc.wait(), err


def _backend_alive(sample_rate: int, channels: int, bytes_per_sample: int, backend: str) -> bool:
    """Probe a backend with a few frames of silence.

    A broken simpleaudio build can play a partial buffer before crashing natively —
    probing first avoids a truncated first attempt echoing before the fallback restarts.
    """
    silence = b"\x00" * (channels * bytes_per_sample * 240)
    code, _ = _run_worker(_worker_args(sample_rate, channels, bytes_per_sample, backend), [silence], [])
    return code == 0


def _play_via_worker(chunks: Iterable[bytes], sample_rate: int, channels: int, bytes_per_sample: int) -> int:
    """Pipe PCM chunks to a playback worker; returns bytes played.

    `sent` accumulates every chunk written so a failed backend can be retried from the
    start — speech audio is small enough to buffer.
    """
    sent: list[bytes] = []
    backends = ["simpleaudio"] + (["winsound"] if sys.platform == "win32" else [])
    err = ""
    for backend in backends:
        if not _backend_alive(sample_rate, channels, bytes_per_sample, backend):
            continue
        # First call streams chunks and buffers them into `sent`; retries replay the buffer.
        source = iter(sent) if sent else chunks
        code, err = _run_worker(_worker_args(sample_rate, channels, bytes_per_sample, backend),
                                source, [] if sent else sent)
        if code == 0:
            return sum(len(c) for c in sent)
    detail = err or "no working playback backend (check your simpleaudio installation)"
    raise typer.BadParameter(f"playback failed: {detail}")


def play_pcm(data: bytes, sample_rate: int, *, channels: int = 1, bytes_per_sample: int = 2) -> int:
    """Play a raw signed 16-bit PCM buffer; returns bytes played."""
    return _play_via_worker([data], sample_rate, channels, bytes_per_sample)


def play_pcm_stream(chunks: Iterable[bytes], sample_rate: int, *, channels: int = 1,
                    bytes_per_sample: int = 2) -> int:
    """Play PCM chunks progressively as an iterable yields them; returns bytes played."""
    return _play_via_worker(chunks, sample_rate, channels, bytes_per_sample)


def play_wav(data: bytes) -> int:
    """Validate and play a complete RIFF/WAV stream; returns bytes played."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            params = wav.getparams()
            frames = wav.readframes(wav.getnframes())
    except (EOFError, wave.Error) as exc:
        raise typer.BadParameter(f"expected WAV audio for playback, got an unreadable stream: {exc}") from None
    play_pcm(frames, params.framerate, channels=params.nchannels, bytes_per_sample=params.sampwidth)
    return len(data)


def play_response(resp: httpx.Response) -> int:
    """Play a completed generate/test audio response; returns the byte count played."""
    data = resp.content
    if resp.headers.get("x-opal-audio-format") == "wav":
        return play_wav(data)
    rate = int(resp.headers.get("x-opal-sample-rate", 0))
    if not rate:
        raise typer.BadParameter("cannot play audio: the response did not declare a sample rate")
    return play_pcm(data, rate)


def _play_simpleaudio(align: int, rate: int, channels: int, bps: int) -> None:
    sa = _simpleaudio()
    pending = b""
    while block := sys.stdin.buffer.read1(1 << 16):
        pending += block
        usable = len(pending) - (len(pending) % align)
        sa.play_buffer(pending[:usable], num_channels=channels,
                       bytes_per_sample=bps, sample_rate=rate).wait_done()
        pending = pending[usable:]
    if pending:
        raise RuntimeError(f"{len(pending)} trailing bytes are not a complete audio frame")


def _play_winsound(rate: int, channels: int, bps: int) -> None:
    try:
        import winsound
    except ImportError:
        raise RuntimeError("winsound is only available on Windows") from None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(bps)
        wav.setframerate(rate)
        wav.writeframes(sys.stdin.buffer.read())
    # PlaySound is synchronous unless SND_ASYNC is given; SND_SYNC was removed in Python 3.13.
    winsound.PlaySound(buf.getvalue(), winsound.SND_MEMORY)


def _worker() -> int:
    """Playback worker: reads raw PCM on stdin, plays it locally.

    `sys.argv`: --rate HZ [--backend simpleaudio|winsound] [--channels N]
                [--bytes-per-sample N]
    """
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--rate", type=int, required=True)
    parser.add_argument("--backend", choices=["simpleaudio", "winsound"], default="simpleaudio")
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument("--bytes-per-sample", type=int, default=2)
    ns = parser.parse_args()
    try:
        if ns.backend == "winsound":
            _play_winsound(ns.rate, ns.channels, ns.bytes_per_sample)
        else:
            _play_simpleaudio(ns.channels * ns.bytes_per_sample, ns.rate, ns.channels,
                              ns.bytes_per_sample)
    except Exception as exc:  # noqa: BLE001 - worker must report, never traceback a broken audio lib
        print(f"{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_worker())
