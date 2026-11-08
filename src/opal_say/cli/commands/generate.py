"""`opal say generate` and the default `opal say TEXT` playback path."""

from __future__ import annotations

import sys
from typing import Any

import httpx
import typer

from opal_say.cli import audio
from opal_say.cli.client import ApiCallError
from opal_say.cli.common import (
    OptionalOutput,
    OptionPairs,
    OptionsJson,
    _audio_summary,
    _call,
    _parse_options,
    _state,
    _write_audio,
)


def _generation_body(text: str, profile: int | None, voice: str | None, engine: str | None, speed: float | None,
                     volume: float | None, output_format: str | None, sample_rate: int | None,
                     option: list[str] | None, options_json: str | None,
                     persona: str | None = None) -> dict[str, Any]:
    if text == "-":
        text = sys.stdin.read()
    body: dict[str, Any] = {"text": text}
    for key, value in (("profile_id", profile), ("voice_id", voice), ("persona", persona), ("engine_id", engine),
                       ("speed", speed),
                       ("volume", volume), ("output_format", output_format), ("sample_rate", sample_rate)):
        if value is not None:
            body[key] = value
    options = _parse_options(option, options_json)
    if options is not None:
        body["engine_options"] = options
    return body


def _stream_response(resp: httpx.Response, output: str | None, play: bool) -> int:
    """Write and/or progressively play a streaming PCM response."""
    if not play:
        return _write_audio(resp, output or "-", streaming=True)
    rate = int(resp.headers.get("x-opal-sample-rate", 0))
    if not rate:
        raise typer.BadParameter("cannot play audio: the response did not declare a sample rate")
    out = None
    if output is not None:
        out = sys.stdout.buffer if output == "-" else open(output, "wb")  # noqa: SIM115
    try:
        def tee():
            for chunk in resp.iter_bytes():
                if out is not None:
                    out.write(chunk)
                    out.flush()
                yield chunk
        return audio.play_pcm_stream(tee(), rate)
    finally:
        if out is not None and out is not sys.stdout.buffer:
            out.close()


def _synthesize(ctx: typer.Context, body: dict[str, Any], output: str | None, stream: bool, play: bool) -> None:
    if play and output == "-":
        raise typer.BadParameter("cannot both write audio to stdout and play it locally")
    if not play and output is None:
        raise typer.BadParameter("supply -o/--output, or play the audio locally")
    if stream:
        try:
            with _state(ctx).client.stream("stream_audio_api_v1_generate_stream_post", json=body) as resp:
                size = _stream_response(resp, output, play)
        except ApiCallError as exc:
            typer.echo(f"Error: {exc.describe()}", err=True)
            raise typer.Exit(1) from None
        except httpx.HTTPError as exc:
            typer.echo(f"Error: stream terminated prematurely; generation failed ({exc})", err=True)
            raise typer.Exit(1) from None
    else:
        resp = _call(ctx, "generate_audio_api_v1_generate_post", json=body)
        size = _write_audio(resp, output) if output is not None else 0
        if play:
            size = audio.play_response(resp)
    if output is not None:
        _audio_summary(resp, output, size)
    else:
        _audio_summary(resp, "the local speaker", size)


def register(say_app: typer.Typer) -> None:
    @say_app.command("speak", hidden=True)
    def speak(
        ctx: typer.Context,
        text: str = typer.Argument(..., help="Plain text to speak ('-' reads stdin)"),
        output: str | None = OptionalOutput,
        stream: bool = typer.Option(False, "--stream", help="Play progressively while audio is synthesized"),
        play: bool = typer.Option(True, "--play/--no-play", help="Play locally (default) or only write output"),
        profile: int = typer.Option(None, "--profile", min=1, help="Profile id (default: active profile)"),
        persona: str = typer.Option(None, "--persona", help="Named persona supplying the voice"),
        voice: str = typer.Option(None, "--voice"),
        engine: str = typer.Option(None, "--engine", help="Advanced engine override"),
        speed: float = typer.Option(None, "--speed", min=0.25, max=4.0),
        volume: float = typer.Option(None, "--volume", min=0.0, max=2.0),
        output_format: str = typer.Option(None, "--format", help="wav or pcm (not with --stream)"),
        sample_rate: int = typer.Option(None, "--sample-rate", min=8000, max=192000),
        option: list[str] = OptionPairs,
        options_json: str = OptionsJson,
    ) -> None:
        """Speak text aloud (the default for 'opal say TEXT')."""
        if stream and output_format is not None:
            raise typer.BadParameter("--format is not supported with --stream (always pcm)")
        if output == "-":  # piping to stdout wins over playback
            play = False
        _synthesize(ctx, _generation_body(text, profile, voice, engine, speed, volume, output_format, sample_rate,
                                        option, options_json, persona=persona), output, stream, play)

    @say_app.command("generate")
    def generate(
        ctx: typer.Context,
        text: str = typer.Argument(..., help="Plain text to speak ('-' reads stdin)"),
        output: str | None = OptionalOutput,
        stream: bool = typer.Option(False, "--stream", help="Use /generate/stream (progressive raw PCM)"),
        play: bool = typer.Option(False, "--play", help="Also play the audio locally (requires 'opal-say[audio]')"),
        profile: int = typer.Option(None, "--profile", min=1, help="Profile id (default: active profile)"),
        persona: str = typer.Option(None, "--persona", help="Named persona supplying the voice"),
        voice: str = typer.Option(None, "--voice"),
        engine: str = typer.Option(None, "--engine", help="Advanced engine override"),
        speed: float = typer.Option(None, "--speed", min=0.25, max=4.0),
        volume: float = typer.Option(None, "--volume", min=0.0, max=2.0),
        output_format: str = typer.Option(None, "--format", help="wav or pcm (not with --stream)"),
        sample_rate: int = typer.Option(None, "--sample-rate", min=8000, max=192000),
        option: list[str] = OptionPairs,
        options_json: str = OptionsJson,
    ) -> None:
        """Generate speech. With --stream, raw S16LE mono PCM is written as it arrives.

        Example: opal say generate "Hello" --stream -o - | aplay -r 22050 -f S16_LE -c 1
        """
        if stream and output_format is not None:
            raise typer.BadParameter("--format is not supported with --stream (always pcm)")
        _synthesize(ctx, _generation_body(text, profile, voice, engine, speed, volume, output_format, sample_rate,
                                        option, options_json, persona=persona), output, stream, play)
