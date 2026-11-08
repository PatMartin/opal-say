"""Info-level commands: version, health, liveness ping and the command tree."""

from __future__ import annotations

import contextlib
import sys

import click
import typer
from typer.main import get_command

from opal_say.cli.common import _call, _emit, _state


def _param_label(p: click.Parameter) -> str:
    # TyperArgument/TyperOption don't subclass click.Argument/click.Option;
    # TyperArgument stores its bare name in opts — only dashed decls are options.
    dashed = [o for o in (*getattr(p, "opts", []), *getattr(p, "secondary_opts", [])) if o.startswith("-")]
    if not dashed:
        name = (p.metavar or p.name or "arg").upper()
        if p.nargs == -1:
            name += "..."
        return name if p.required else f"[{name}]"
    opts = sorted(p.opts, key=lambda o: o.startswith("--"))  # short flags first
    flags = "/".join((*opts, *p.secondary_opts))
    if getattr(p, "is_flag", False) or getattr(p, "count", False):
        label = flags
    else:
        if p.metavar:
            meta = p.metavar
        elif isinstance(p.type, click.Choice):
            meta = "|".join(p.type.choices)
        else:
            meta = (p.type.name or "value").upper() or "VALUE"
        label = f"{flags} {meta}"
    return label if p.required else f"[{label}]"


def _walk(group: click.Group, prefix: str) -> None:
    commands = [(n, c) for n, c in group.commands.items() if not c.hidden]
    for i, (name, cmd) in enumerate(commands):
        last = i == len(commands) - 1
        params = " ".join(_param_label(p) for p in cmd.params if not getattr(p, "hidden", False))
        typer.echo(f"{prefix}{'└── ' if last else '├── '}{name}" + (f" {params}" if params else ""))
        if getattr(cmd, "commands", None):  # TyperGroup doesn't subclass click.Group
            _walk(cmd, prefix + ("    " if last else "│   "))


def _health(ctx: typer.Context) -> None:
    data = _call(ctx, "opal_health_get").json()
    if _state(ctx).json_output:
        _emit(ctx, data)
        return
    typer.echo(f"status: {data['status']}  version: {data['version']}  active_profile_id: {data.get('active_profile_id')}")
    _emit(ctx, data["engines"], ["engine_id", "enabled", "available", "message"])


def register(say_app: typer.Typer) -> None:
    info_app = typer.Typer(
        help="Service identity: version, health and liveness probes.",
        invoke_without_command=True,
        no_args_is_help=False,
    )

    @info_app.callback(invoke_without_command=True)
    def info_root(ctx: typer.Context) -> None:
        """Service identity and API version (default for bare 'info')."""
        if ctx.invoked_subcommand is None:
            _emit(ctx, _call(ctx, "opal_info_get").json())
            raise typer.Exit()

    @info_app.command("version")
    def info_version(ctx: typer.Context) -> None:
        """Service identity and version."""
        _emit(ctx, _call(ctx, "opal_info_get").json())

    @info_app.command("health")
    def info_health(ctx: typer.Context) -> None:
        """Service readiness and engine state."""
        _health(ctx)

    @info_app.command("live")
    def info_live(ctx: typer.Context) -> None:
        """Liveness probe."""
        _emit(ctx, _call(ctx, "opal_health_live_get").json())

    @info_app.command("ready")
    def info_ready(ctx: typer.Context) -> None:
        """Readiness probe (reports the body on 503)."""
        from opal_say.cli.client import ApiCallError

        try:
            _emit(ctx, _call(ctx, "opal_health_ready_get").json())
        except ApiCallError as e:
            if e.status != 503:
                raise
            _emit(ctx, e.body)

    @info_app.command("ping")
    def info_ping(ctx: typer.Context) -> None:
        """Liveness check."""
        typer.echo(_call(ctx, "opal_info_ping").json()["status"])

    say_app.add_typer(info_app, name="info")

    @say_app.command("tree")
    def tree() -> None:
        """Print an ASCII tree of every CLI command and its arguments.
        Optional arguments appear in [square brackets]."""
        with contextlib.suppress(AttributeError, ValueError):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        root = get_command(say_app)
        typer.echo("opal-say " + " ".join(_param_label(p) for p in root.params))
        _walk(root, "")
