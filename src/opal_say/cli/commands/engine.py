"""`opal say engine ...` — discover and configure installed TTS engine."""

from __future__ import annotations

from typing import Any

import typer

from opal_say.cli.common import ENGINE_COLUMNS, OptionPairs, OptionsJson, _call, _emit, _parse_options, _validation

engine_app = typer.Typer(help="Discover and configure installed TTS engine.", no_args_is_help=True)


@engine_app.command("list")
def engine_list(ctx: typer.Context) -> None:
    """List installed engine."""
    _emit(ctx, _call(ctx, "opal_engine_list").json(), ENGINE_COLUMNS)


@engine_app.command("get")
def engine_get(ctx: typer.Context, engine_id: str) -> None:
    """Show one engine."""
    _emit(ctx, _call(ctx, "opal_engine_get", path_params={"engine_id": engine_id}).json())


@engine_app.command("update")
def engine_update(
    ctx: typer.Context,
    engine_id: str,
    enabled: bool = typer.Option(None, "--enable/--disable", help="Enable or disable routing to the engine"),
    option: list[str] = OptionPairs,
    options_json: str = OptionsJson,
) -> None:
    """Update engine configuration (options are merged; a null value removes a key)."""
    body: dict[str, Any] = {}
    if enabled is not None:
        body["enabled"] = enabled
    options = _parse_options(option, options_json)
    if options is not None:
        body["options"] = options
    if not body:
        raise typer.BadParameter("nothing to update: use --enable/--disable or --option")
    resp = _call(ctx, "opal_engine_patch", path_params={"engine_id": engine_id}, json=body)
    _emit(ctx, resp.json())


@engine_app.command("validate")
def engine_validate(ctx: typer.Context, engine_id: str) -> None:
    """Validate an engine's runtime and configuration."""
    _validation(
        ctx, _call(ctx, "opal_engine_validate", path_params={"engine_id": engine_id})
    )


def register(say_app: typer.Typer) -> None:
    say_app.add_typer(engine_app, name="engine")
