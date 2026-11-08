"""`opal say setting ...` — service-wide key/value setting."""

from __future__ import annotations

import json
from typing import Any

import typer

from opal_say.cli.common import _call, _emit

setting_app = typer.Typer(help="Service-wide key/value setting.", no_args_is_help=True)


@setting_app.command("list")
def setting_list(ctx: typer.Context) -> None:
    """Show all stored setting."""
    _emit(ctx, _call(ctx, "opal_settings_get").json())


@setting_app.command("get")
def setting_get(ctx: typer.Context, key: str) -> None:
    """Show one setting."""
    _emit(ctx, _call(ctx, "opal_setting_get", path_params={"key": key}).json())


@setting_app.command("set")
def setting_set(ctx: typer.Context, key: str, value: str) -> None:
    """Create or replace a setting. The value is sent as native JSON when it
    parses as such (`5`, `true`, `{"a": 1}`); otherwise it is sent as a string."""
    try:
        parsed: Any = json.loads(value)
    except ValueError:
        parsed = value
    _emit(ctx, _call(ctx, "opal_setting_put", path_params={"key": key},
                     json={"value": parsed}).json())


@setting_app.command("delete")
def setting_delete(ctx: typer.Context, key: str) -> None:
    """Delete a setting so the default applies."""
    _call(ctx, "opal_setting_delete", path_params={"key": key})
    typer.echo(f"setting '{key}' deleted")


@setting_app.command("patch")
def setting_patch(ctx: typer.Context, payload: str = typer.Option(
    ..., "--json", help='JSON object of changes, e.g. \'{"default_speed": 1.2}\' (null deletes a key).'
)) -> None:
    """Atomically merge-patch the whole setting document."""
    _emit(ctx, _call(ctx, "opal_settings_patch", json=json.loads(payload)).json())


def register(say_app: typer.Typer) -> None:
    say_app.add_typer(setting_app, name="setting")
