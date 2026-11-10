"""`opal say service ...` — managed server lifecycle (contract `service` tag).

Each command calls its REST operation first so remote management works; when the
API is unreachable the command falls back to the same pidfile/log bookkeeping
locally, which is what lets `service start` cold-start a stopped service.
"""

from __future__ import annotations

import httpx
import typer

from opal_say.cli.client import ApiCallError, ApiClient
from opal_say.cli.common import _emit, _state
from opal_say.config import load_config
from opal_say.service.lifecycle import ServiceProcessManager

service_app = typer.Typer(help="Start, stop and inspect the managed server process.", no_args_is_help=True)


def _client(ctx: typer.Context) -> ApiClient:
    return _state(ctx).client


def _manager() -> ServiceProcessManager:
    return ServiceProcessManager(load_config())


def _api_error(exc: ApiCallError) -> None:
    typer.echo(f"Error: {exc.describe()}", err=True)
    raise typer.Exit(1) from None


@service_app.command("start")
def service_start(
    ctx: typer.Context,
    append: bool = typer.Option(False, "--append", help="Append to the service log instead of truncating it"),
) -> None:
    """Start the managed server in the background (restarts one already running)."""
    try:
        _emit(ctx, _client(ctx).call("opal_service_start", json={"append": append}).json())
        return
    except ApiCallError as exc:
        _api_error(exc)
    except httpx.HTTPError:
        pass  # API unreachable: spawn the managed server locally
    proc = _manager().launch(append=append)
    typer.echo(f"server started (pid {proc.pid}) — log: {_manager().log_file}")


@service_app.command("stop")
def service_stop(ctx: typer.Context) -> None:
    """Stop the managed server if it is running."""
    try:
        _emit(ctx, _client(ctx).call("opal_service_stop").json())
        return
    except ApiCallError as exc:
        _api_error(exc)
    except httpx.HTTPError:
        pass  # API unreachable: stop the tracked pid locally
    status, _ = _manager().stop()
    if status["previous_pid"] is None:
        typer.echo("no managed server running")
    else:
        typer.echo(f"server stopped (pid {status['previous_pid']})")


@service_app.command("log")
def service_log(ctx: typer.Context, lines: int = typer.Option(10, "--lines", "-n", min=1, max=1000)) -> None:
    """Tail the service log file (default: last 10 lines)."""
    try:
        data = _client(ctx).call("opal_service_log", params={"lines": lines}).json()
        if _state(ctx).json_output:
            _emit(ctx, data)
        else:
            for line in data["lines"]:
                typer.echo(line)
        return
    except ApiCallError as exc:
        _api_error(exc)
    except httpx.HTTPError:
        pass  # API unreachable: read the local log file
    tail = _manager().tail_log(lines)
    if tail is None:
        typer.echo(f"no log file at {_manager().log_file}", err=True)
        raise typer.Exit(1)
    for line in tail["lines"]:
        typer.echo(line)


def register(say_app: typer.Typer) -> None:
    say_app.add_typer(service_app, name="service")
