"""Root-level commands: health, service info, liveness ping and the command tree."""

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


def register(say_app: typer.Typer) -> None:
    @say_app.command("health")
    def health(ctx: typer.Context, live: bool = typer.Option(False, "--live", help="Liveness probe."),
               ready: bool = typer.Option(False, "--ready", help="Readiness probe.")) -> None:
        """Service readiness and engine state."""
        if live:
            _emit(ctx, _call(ctx, "opal_health_live_get").json())
            return
        if ready:
            from opal_say.cli.client import ApiCallError

            try:
                _emit(ctx, _call(ctx, "opal_health_ready_get").json())
            except ApiCallError as e:
                if e.status != 503:
                    raise
                _emit(ctx, e.body)
            return
        data = _call(ctx, "opal_health_get").json()
        if _state(ctx).json_output:
            _emit(ctx, data)
            return
        typer.echo(f"status: {data['status']}  version: {data['version']}  active_profile_id: {data.get('active_profile_id')}")
        _emit(ctx, data["engines"], ["engine_id", "enabled", "available", "message"])

    @say_app.command("info")
    def info(ctx: typer.Context) -> None:
        """Service identity and API version."""
        _emit(ctx, _call(ctx, "root__get").json())

    @say_app.command("ping")
    def ping(ctx: typer.Context) -> None:
        """Liveness check."""
        typer.echo(_call(ctx, "healthz_healthz_get").json()["status"])

    @say_app.command("tree")
    def tree() -> None:
        """Print an ASCII tree of every CLI command and its arguments.
        Optional arguments appear in [square brackets]."""
        with contextlib.suppress(AttributeError, ValueError):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        root = get_command(say_app)
        typer.echo("opal-say " + " ".join(_param_label(p) for p in root.params))
        _walk(root, "")
