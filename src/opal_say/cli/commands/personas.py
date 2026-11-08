"""`opal say personas ...` — manage named personas bound to registered voices."""

from __future__ import annotations

from typing import Any

import typer

from opal_say.cli.common import PERSONA_COLUMNS, _call, _emit

personas_app = typer.Typer(help="Manage named personas bound to registered voices.", no_args_is_help=True)


@personas_app.command("list")
def personas_list(
    ctx: typer.Context,
    enabled: bool = typer.Option(None, "--enabled/--disabled", help="Filter by enabled state"),
) -> None:
    """List registered personas."""
    resp = _call(ctx, "list_personas_api_v1_personas__get",
                 params={"enabled": None if enabled is None else str(enabled).lower()})
    _emit(ctx, resp.json(), PERSONA_COLUMNS)


@personas_app.command("add")
def personas_add(
    ctx: typer.Context,
    persona_id: str,
    name: str = typer.Option(..., "--name", help="Human-readable name"),
    voice: str = typer.Option(..., "--voice", help="Registered voice_id the persona speaks with"),
    description: str = typer.Option(None, "--description"),
    enabled: bool = typer.Option(True, "--enabled/--disabled"),
) -> None:
    """Register a persona bound to a voice."""
    body: dict[str, Any] = {"persona_id": persona_id, "name": name, "voice_id": voice, "enabled": enabled}
    if description is not None:
        body["description"] = description
    _emit(ctx, _call(ctx, "create_persona_api_v1_personas__post", json=body).json(), PERSONA_COLUMNS)


@personas_app.command("get")
def personas_get(ctx: typer.Context, persona_id: str) -> None:
    """Show one persona."""
    _emit(ctx, _call(ctx, "get_persona_api_v1_personas__persona_id__get",
                     path_params={"persona_id": persona_id}).json(), PERSONA_COLUMNS)


@personas_app.command("update")
def personas_update(
    ctx: typer.Context,
    persona_id: str,
    name: str = typer.Option(None, "--name"),
    voice: str = typer.Option(None, "--voice", help="Rebind to a different registered voice_id"),
    description: str = typer.Option(None, "--description"),
    clear_description: bool = typer.Option(False, "--clear-description"),
    enabled: bool = typer.Option(None, "--enabled/--disabled"),
) -> None:
    """Update a persona."""
    body: dict[str, Any] = {}
    for key, value in (("name", name), ("voice_id", voice), ("description", description), ("enabled", enabled)):
        if value is not None:
            body[key] = value
    if clear_description:
        body["description"] = None
    if not body:
        raise typer.BadParameter("nothing to update")
    resp = _call(ctx, "update_persona_api_v1_personas__persona_id__patch",
                 path_params={"persona_id": persona_id}, json=body)
    _emit(ctx, resp.json(), PERSONA_COLUMNS)


@personas_app.command("remove")
def personas_remove(ctx: typer.Context, persona_id: str) -> None:
    """Delete a persona (the bound voice registration is unaffected)."""
    _call(ctx, "delete_persona_api_v1_personas__persona_id__delete", path_params={"persona_id": persona_id})
    typer.echo(f"persona '{persona_id}' deleted")


def register(say_app: typer.Typer) -> None:
    say_app.add_typer(personas_app, name="personas")
