"""`opal say persona ...` — manage named persona bound to registered voice."""

from __future__ import annotations

from typing import Any

import typer

from opal_say.cli.common import PERSONA_COLUMNS, _call, _emit, _validation

persona_app = typer.Typer(help="Manage named persona bound to registered voice.", no_args_is_help=True)


@persona_app.command("list")
def persona_list(
    ctx: typer.Context,
    enabled: bool = typer.Option(None, "--enabled/--no-enabled", help="Filter by enabled state"),
) -> None:
    """List registered persona."""
    resp = _call(ctx, "opal_persona_list",
                 params={"enabled": None if enabled is None else str(enabled).lower()})
    _emit(ctx, resp.json(), PERSONA_COLUMNS)


@persona_app.command("add")
def persona_add(
    ctx: typer.Context,
    persona_id: str = typer.Option(..., "--persona-id"),
    if_match: str = typer.Option(None, "--if-match"),
    name: str = typer.Option(..., "--name", help="Human-readable name"),
    voice: str = typer.Option(None, "--voice-id", help="Registered voice_id the persona speaks with"),
    description: str = typer.Option(None, "--description"),
    enabled: bool = typer.Option(True, "--enabled/--no-enabled"),
) -> None:
    """Register a persona bound to a voice."""
    body: dict[str, Any] = {"persona_id": persona_id, "name": name, "voice_id": voice, "enabled": enabled}
    if description is not None:
        body["description"] = description
    _emit(ctx, _call(ctx, "opal_persona_create", json=body, if_match=if_match).json(), PERSONA_COLUMNS)


@persona_app.command("show")
def persona_get(ctx: typer.Context, persona_id: str) -> None:
    """Show one persona."""
    _emit(ctx, _call(ctx, "opal_persona_get",
                     path_params={"persona_id": persona_id}).json(), PERSONA_COLUMNS)


@persona_app.command("update")
def persona_update(
    ctx: typer.Context,
    persona_id: str,
    if_match: str = typer.Option(None, "--if-match"),
    name: str = typer.Option(None, "--name"),
    voice: str = typer.Option(None, "--voice-id", help="Rebind to a different registered voice_id"),
    clear_voice: bool = typer.Option(False, "--clear-voice-id"),
    description: str = typer.Option(None, "--description"),
    clear_description: bool = typer.Option(False, "--clear-description"),
    enabled: bool = typer.Option(None, "--enabled/--no-enabled"),
) -> None:
    """Update a persona."""
    body: dict[str, Any] = {}
    for key, value in (("name", name), ("voice_id", voice), ("description", description), ("enabled", enabled)):
        if value is not None:
            body[key] = value
    if clear_description:
        body["description"] = None
    if clear_voice:
        body["voice_id"] = None
    if not body:
        raise typer.BadParameter("nothing to update")
    resp = _call(ctx, "opal_persona_patch",
                 path_params={"persona_id": persona_id}, json=body, if_match=if_match)
    _emit(ctx, resp.json(), PERSONA_COLUMNS)


@persona_app.command("validate")
def persona_validate(ctx: typer.Context, persona_id: str) -> None:
    """Validate that a persona resolves: registered, enabled, and its voice usable."""
    _validation(
        ctx, _call(ctx, "opal_persona_validate", path_params={"persona_id": persona_id})
    )


@persona_app.command("remove")
def persona_remove(ctx: typer.Context, persona_id: str, if_match: str = typer.Option(None, "--if-match")) -> None:
    """Delete a persona (the bound voice registration is unaffected)."""
    _call(ctx, "opal_persona_delete", path_params={"persona_id": persona_id}, if_match=if_match)
    typer.echo(f"persona '{persona_id}' deleted")


def register(say_app: typer.Typer) -> None:
    say_app.add_typer(persona_app, name="persona")
