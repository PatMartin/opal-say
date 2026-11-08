"""`opal say profile ...` — manage reusable generation profile."""

from __future__ import annotations

from typing import Any

import typer

from opal_say.cli.common import PROFILE_COLUMNS, OptionPairs, OptionsJson, _call, _emit, _parse_options

profile_app = typer.Typer(help="Manage reusable generation profile.", no_args_is_help=True)


def _profile_body(
    name: str | None, description: str | None, voice: str | None, speed: float | None, volume: float | None,
    output_format: str | None, sample_rate: int | None, active: bool | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    for key, value in (("name", name), ("description", description), ("voice_id", voice), ("speed", speed),
                       ("volume", volume), ("output_format", output_format), ("sample_rate", sample_rate),
                       ("is_active", active)):
        if value is not None:
            body[key] = value
    return body


@profile_app.command("list")
def profile_list(ctx: typer.Context) -> None:
    """List profile."""
    _emit(ctx, _call(ctx, "opal_profile_list").json(), PROFILE_COLUMNS)


@profile_app.command("add")
def profile_add(
    ctx: typer.Context,
    name: str,
    voice: str = typer.Option(..., "--voice", help="Registered voice_id"),
    description: str = typer.Option(None, "--description"),
    speed: float = typer.Option(None, "--speed", min=0.25, max=4.0),
    volume: float = typer.Option(None, "--volume", min=0.0, max=2.0),
    output_format: str = typer.Option(None, "--format", help="wav or pcm"),
    sample_rate: int = typer.Option(None, "--sample-rate", min=8000, max=192000),
    active: bool = typer.Option(False, "--active", help="Activate this profile"),
    option: list[str] = OptionPairs,
    options_json: str = OptionsJson,
) -> None:
    """Create a profile."""
    body = _profile_body(name, description, voice, speed, volume, output_format, sample_rate, active)
    body["engine_options"] = _parse_options(option, options_json) or {}
    _emit(ctx, _call(ctx, "opal_profile_create", json=body).json())


@profile_app.command("get")
def profile_get(ctx: typer.Context, profile_id: int) -> None:
    """Show one profile."""
    _emit(ctx, _call(ctx, "opal_profile_get",
                     path_params={"profile_id": profile_id}).json())


@profile_app.command("update")
def profile_update(
    ctx: typer.Context,
    profile_id: int,
    name: str = typer.Option(None, "--name"),
    voice: str = typer.Option(None, "--voice"),
    description: str = typer.Option(None, "--description"),
    clear_description: bool = typer.Option(False, "--clear-description"),
    speed: float = typer.Option(None, "--speed", min=0.25, max=4.0),
    volume: float = typer.Option(None, "--volume", min=0.0, max=2.0),
    output_format: str = typer.Option(None, "--format", help="wav or pcm"),
    sample_rate: int = typer.Option(None, "--sample-rate", min=8000, max=192000),
    native_rate: bool = typer.Option(False, "--native-rate", help="Clear sample_rate (service chooses)"),
    active: bool = typer.Option(None, "--active/--inactive"),
    option: list[str] = OptionPairs,
    options_json: str = OptionsJson,
) -> None:
    """Partially update a profile (engine_options are replaced)."""
    body = _profile_body(name, description, voice, speed, volume, output_format, sample_rate, active)
    if clear_description:
        body["description"] = None
    if native_rate:
        body["sample_rate"] = None
    options = _parse_options(option, options_json)
    if options is not None:
        body["engine_options"] = options
    if not body:
        raise typer.BadParameter("nothing to update")
    resp = _call(ctx, "opal_profile_patch", path_params={"profile_id": profile_id},
                 json=body)
    _emit(ctx, resp.json())


@profile_app.command("remove")
def profile_remove(ctx: typer.Context, profile_id: int) -> None:
    """Delete a (non-active) profile."""
    _call(ctx, "opal_profile_delete", path_params={"profile_id": profile_id})
    typer.echo(f"profile {profile_id} deleted")


@profile_app.command("activate")
def profile_activate(ctx: typer.Context, profile_id: int) -> None:
    """Make a profile the active one."""
    resp = _call(ctx, "opal_profile_activate",
                 path_params={"profile_id": profile_id})
    _emit(ctx, resp.json())


def register(say_app: typer.Typer) -> None:
    say_app.add_typer(profile_app, name="profile")
