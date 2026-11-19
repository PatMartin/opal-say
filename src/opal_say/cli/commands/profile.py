"""`opal say profile ...` — manage reusable generation profile."""

from __future__ import annotations

from typing import Any

import typer

from opal_say.cli.common import PROFILE_COLUMNS, _call, _emit, read_document

profile_app = typer.Typer(help="Manage reusable generation profile.", no_args_is_help=True)


def _profile_body(name: str | None, description: str | None, active: bool | None,
                  input_file: str | None, shared: str | None, module: str | None) -> dict[str, Any]:
    if input_file:
        if any(value is not None for value in (name, description, active, shared, module)):
            raise typer.BadParameter("--in is exclusive with field options")
        return read_document(input_file)
    body: dict[str, Any] = {}
    for key, value in (("name", name), ("description", description), ("is_active", active)):
        if value is not None:
            body[key] = value
    if shared:
        body["shared"] = read_document(shared)
    if module:
        body["modules"] = {"opal-say": read_document(module)}
    return body


@profile_app.command("list")
def profile_list(ctx: typer.Context) -> None:
    """List profile."""
    _emit(ctx, _call(ctx, "opal_profile_list").json(), PROFILE_COLUMNS)


@profile_app.command("add")
def profile_add(
    ctx: typer.Context,
    name: str = typer.Option(None, "--name"),
    description: str = typer.Option(None, "--description"),
    active: bool = typer.Option(None, "--is-active/--no-is-active"),
    input_file: str = typer.Option(None, "--in"),
    shared: str = typer.Option(None, "--shared"),
    module: str = typer.Option(None, "--module"),
) -> None:
    """Create a profile."""
    body = _profile_body(name, description, active, input_file, shared, module)
    if "name" not in body:
        raise typer.BadParameter("supply --name or a complete profile with --in")
    _emit(ctx, _call(ctx, "opal_profile_create", json=body).json())


@profile_app.command("show")
def profile_get(ctx: typer.Context, profile_id: str) -> None:
    """Show one profile."""
    _emit(ctx, _call(ctx, "opal_profile_get", path_params={"profile_id": profile_id}).json())


@profile_app.command("update")
def profile_update(
    ctx: typer.Context,
    profile_id: str,
    name: str = typer.Option(None, "--name"),
    description: str = typer.Option(None, "--description"),
    active: bool = typer.Option(None, "--is-active/--no-is-active"),
    input_file: str = typer.Option(None, "--in"),
    shared: str = typer.Option(None, "--shared"),
    module: str = typer.Option(None, "--module"),
) -> None:
    """Partially update a profile (engine_options are replaced)."""
    body = _profile_body(name, description, active, input_file, shared, module)
    if not body:
        raise typer.BadParameter("nothing to update")
    _emit(ctx, _call(ctx, "opal_profile_patch", path_params={"profile_id": profile_id}, json=body).json())


@profile_app.command("remove")
def profile_remove(ctx: typer.Context, profile_id: str) -> None:
    """Delete a (non-active) profile."""
    _call(ctx, "opal_profile_delete", path_params={"profile_id": profile_id})
    typer.echo(f"profile {profile_id} deleted")


@profile_app.command("activate")
def profile_activate(ctx: typer.Context, profile_id: str) -> None:
    """Make a profile the active one."""
    _emit(ctx, _call(ctx, "opal_profile_activate", path_params={"profile_id": profile_id}).json())


def register(say_app: typer.Typer) -> None:
    say_app.add_typer(profile_app, name="profile")
