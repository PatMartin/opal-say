"""Info-level commands: version, health, liveness ping and the command tree."""

from __future__ import annotations

import contextlib
import sys

import click
import typer
from rich.console import Console
from rich.text import Text
from rich.tree import Tree
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


def _node_label(name: str, cmd: click.Command) -> Text:
    label = Text(name, style="bold cyan" if getattr(cmd, "commands", None) else "cyan")
    for p in cmd.params:
        if getattr(p, "hidden", False):
            continue
        label.append(f" {_param_label(p)}", style="yellow" if p.required else "dim")
    return label


def _grow(node: Tree, group: click.Group) -> None:
    for name, cmd in group.commands.items():
        if cmd.hidden:
            continue
        child = node.add(_node_label(name, cmd))
        if getattr(cmd, "commands", None):  # TyperGroup doesn't subclass click.Group
            _grow(child, cmd)


def _dump_ascii(node: Tree, prefix: str) -> None:
    for i, child in enumerate(node.children):
        last = i == len(node.children) - 1
        typer.echo(f"{prefix}{'`-- ' if last else '|-- '}{child.label}")
        _dump_ascii(child, prefix + ("    " if last else "|   "))


def _health(ctx: typer.Context) -> None:
    data = _call(ctx, "opal_health_get").json()
    if _state(ctx).json_output:
        _emit(ctx, data)
        return
    typer.echo(f"status: {data['status']}  version: {data['version']}  active_profile_id: {data.get('active_profile_id')}")
    _emit(ctx, data["details"]["engines"], ["engine_id", "enabled", "available", "message"])


def register(say_app: typer.Typer) -> None:
    info_app = typer.Typer(help="Service health and identity; use info health.", no_args_is_help=True)

    @info_app.command("health")
    def info_health(ctx: typer.Context) -> None:
        """Service readiness and engine state."""
        _health(ctx)

    say_app.add_typer(info_app, name="info")

    @say_app.command("tree")
    def tree(
        ascii_output: bool = typer.Option(False, "--ascii", help="Plain ASCII tree: no box-drawing, no color."),
    ) -> None:
        """Print a color-coded tree of every CLI command and its arguments.
        Optional arguments appear in square brackets."""
        with contextlib.suppress(AttributeError, ValueError):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        root = get_command(say_app)
        label = Text("opal-say", style="bold green")
        if params := " ".join(_param_label(p) for p in root.params):
            label.append(f" {params}", style="dim")
        t = Tree(label, guide_style="blue")
        _grow(t, root)
        if ascii_output:
            typer.echo(t.label.plain)
            _dump_ascii(t, "")
        else:
            Console().print(t)
