"""`opal say settings ...` — service-wide key/value settings."""

from __future__ import annotations

import json
from typing import Any

import typer

from opal_say.cli.common import _call, _emit

settings_app = typer.Typer(help="Service-wide key/value settings.", no_args_is_help=True)


@settings_app.command("list")
def settings_list(ctx: typer.Context) -> None:
    """Show all stored settings."""
    _emit(ctx, _call(ctx, "get_all_settings_api_v1_settings__get").json())


@settings_app.command("get")
def settings_get(ctx: typer.Context, key: str) -> None:
    """Show one setting."""
    _emit(ctx, _call(ctx, "get_setting_api_v1_settings__key__get", path_params={"key": key}).json())


@settings_app.command("set")
def settings_set(ctx: typer.Context, key: str, value: str) -> None:
    """Create or replace a setting. The value is sent as native JSON when it
    parses as such (`5`, `true`, `{"a": 1}`); otherwise it is sent as a string."""
    try:
        parsed: Any = json.loads(value)
    except ValueError:
        parsed = value
    _emit(ctx, _call(ctx, "upsert_setting_api_v1_settings__key__put", path_params={"key": key},
                     json={"value": parsed}).json())


@settings_app.command("delete")
def settings_delete(ctx: typer.Context, key: str) -> None:
    """Delete a setting so the default applies."""
    _call(ctx, "delete_setting_api_v1_settings__key__delete", path_params={"key": key})
    typer.echo(f"setting '{key}' deleted")


@settings_app.command("patch")
def settings_patch(ctx: typer.Context, payload: str = typer.Option(
    ..., "--json", help='JSON object of changes, e.g. \'{"default_speed": 1.2}\' (null deletes a key).'
)) -> None:
    """Atomically merge-patch the whole settings document."""
    _emit(ctx, _call(ctx, "patch_talk_settings", json=json.loads(payload)).json())


def register(say_app: typer.Typer) -> None:
    say_app.add_typer(settings_app, name="settings")
