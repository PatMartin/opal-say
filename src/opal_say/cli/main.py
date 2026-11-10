"""`opal say ...` command line. Every command calls the OPAL Say REST API (see COMMANDS)."""

from __future__ import annotations

import typer

from opal_say.cli.client import api_url_from_env
from opal_say.cli.commands import backup, engine, generate, persona, profile, root, service, setting, voice
from opal_say.cli.common import CliState, client_factory  # noqa: F401 - re-exported; tests patch main.client_factory

# REST operationId -> CLI command (relative to `opal say`). Verified by the parity tests.
COMMANDS: dict[str, str] = {
    "opal_engine_list": "engine list",
    "opal_engine_get": "engine get",
    "opal_engine_patch": "engine update",
    "opal_engine_validate": "engine validate",
    "opal_voice_list": "voice list",
    "opal_voice_create": "voice add",
    "opal_voice_get": "voice get",
    "opal_voice_patch": "voice update",
    "opal_voice_delete": "voice remove",
    "opal_voice_validate": "voice validate",
    "opal_voice_test": "voice test",
    "opal_profile_list": "profile list",
    "opal_profile_create": "profile add",
    "opal_profile_get": "profile get",
    "opal_profile_patch": "profile update",
    "opal_profile_delete": "profile remove",
    "opal_profile_activate": "profile activate",
    "opal_persona_list": "persona list",
    "opal_persona_create": "persona add",
    "opal_persona_get": "persona get",
    "opal_persona_validate": "persona validate",
    "opal_persona_patch": "persona update",
    "opal_persona_delete": "persona remove",
    "opal_generate": "generate",
    "opal_generate_stream": "generate --stream",
    "opal_setting_list": "setting list",
    "opal_setting_get": "setting get",
    "opal_setting_put": "setting set",
    "opal_setting_delete": "setting delete",
    "opal_health_get": "info health",
    "opal_health_live_get": "info live",
    "opal_health_ready_get": "info ready",
    "opal_setting_patch": "setting patch",
    "opal_backup_validate": "backup validate --payload",
    "opal_backup_list": "backup list",
    "opal_backup_create": "backup create",
    "opal_backup_download": "backup download",
    "opal_backup_restore": "backup restore",
    "opal_backup_delete": "backup delete",
    "opal_backup_export": "backup export",
    "opal_backup_import": "backup import",
    "opal_backup_validate_stored": "backup validate",
    "opal_info_get": "info version",
    "opal_info_ping": "info ping",
    "opal_service_start": "service start",
    "opal_service_stop": "service stop",
    "opal_service_log": "service log",
}


say_app = typer.Typer(
    help="OPAL Say text-to-speech (talks to the opal-say-svc REST API).",
    epilog="Global options (--api-url, --json, --timeout) go before the command group, "
           "e.g. 'opal say --json voice list'.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


@say_app.callback()
def say_callback(
    ctx: typer.Context,
    api_url: str = typer.Option(None, "--api-url", help="REST API base URL [env: OPAL_SAY_API_URL]"),
    json_output: bool = typer.Option(False, "--json", help="Print raw JSON responses"),
    timeout: float = typer.Option(120.0, "--timeout", help="HTTP timeout in seconds"),
) -> None:
    ctx.obj = CliState(client_factory(api_url or api_url_from_env(), timeout), json_output)


for _module in (engine, voice, profile, persona, generate, setting, backup, service, root):
    _module.register(say_app)


# -- `opal` root -----------------------------------------------------------------

app = typer.Typer(help="OPAL command line.", no_args_is_help=True, pretty_exceptions_enable=False)
app.add_typer(say_app, name="say")

try:  # Share the `opal` root with the sister OPAL Listen CLI when it is installed.
    from opal_listener.cli.main import listen_app  # type: ignore[import-not-found]

    app.add_typer(listen_app, name="listen")
except Exception:  # noqa: BLE001
    pass


if __name__ == "__main__":
    app()
