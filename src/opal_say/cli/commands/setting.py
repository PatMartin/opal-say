"""`opal say config setting ...` — service-wide key/value setting.

Canonical spellings come from the contract's x-opal-cli bindings:
`config setting list|show|set|update|remove`, with `config setting patch`
(alias of `update`) and flat `setting get`/`setting delete` as declared aliases.
"""

from __future__ import annotations

import json
from typing import Any

import typer

from opal_say.cli.common import _call, _emit

config_app = typer.Typer(help="Configuration resources.", no_args_is_help=True)
config_setting_app = typer.Typer(help="Service-wide key/value setting.", no_args_is_help=True)
# Flat `setting` group: only the two spellings the contract declares as aliases.
setting_app = typer.Typer(
    help="Service-wide key/value setting (aliases for `config setting ...`).",
    no_args_is_help=True,
)


def _list(ctx: typer.Context) -> None:
    """Show all stored setting."""
    _emit(ctx, _call(ctx, "opal_setting_list").json())


def _show(ctx: typer.Context, key: str) -> None:
    """Show one setting."""
    _emit(ctx, _call(ctx, "opal_setting_get", path_params={"key": key}).json())


def _set(ctx: typer.Context, key: str, value: str) -> None:
    """Create or replace a setting. The value is sent as native JSON when it
    parses as such (`5`, `true`, `{"a": 1}`); otherwise it is sent as a string."""
    try:
        parsed: Any = json.loads(value)
    except ValueError:
        parsed = value
    _emit(ctx, _call(ctx, "opal_setting_put", path_params={"key": key},
                     json={"value": parsed}).json())


def _update(ctx: typer.Context, payload: str = typer.Option(
    ..., "--json", help='JSON object of changes, e.g. \'{"default_speed": 1.2}\' (null deletes a key).'
)) -> None:
    """Atomically merge-patch the whole setting document."""
    _emit(ctx, _call(ctx, "opal_setting_patch", json=json.loads(payload)).json())


def _remove(ctx: typer.Context, key: str) -> None:
    """Delete a setting so the default applies."""
    _call(ctx, "opal_setting_delete", path_params={"key": key})
    typer.echo(f"setting '{key}' deleted")


# Canonical tree: `opal say config setting ...`
config_setting_app.command("list")(_list)
config_setting_app.command("show")(_show)
config_setting_app.command("set")(_set)
config_setting_app.command("update")(_update)
config_setting_app.command("patch", hidden=True)(_update)  # declared alias of `update`
config_setting_app.command("remove")(_remove)
config_app.add_typer(config_setting_app, name="setting")

# Declared flat aliases: `setting get` and `setting delete`.
setting_app.command("get")(_show)
setting_app.command("delete")(_remove)


def register(say_app: typer.Typer) -> None:
    say_app.add_typer(config_app, name="config")
    say_app.add_typer(setting_app, name="setting")
