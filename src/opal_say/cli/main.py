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
    "validate_engine": "engine validate",
    "list_voices": "voice list",
    "create_voice": "voice add",
    "show_voice": "voice get",
    "update_voice": "voice update",
    "remove_voice": "voice remove",
    "validate_voice": "voice validate",
    "test_voice": "voice test",
    "opal_profile_list": "profile list",
    "opal_profile_create": "profile add",
    "opal_profile_get": "profile show",
    "opal_profile_patch": "profile update",
    "opal_profile_delete": "profile remove",
    "opal_profile_activate": "profile activate",
    "opal_persona_list": "persona list",
    "opal_persona_create": "persona add",
    "opal_persona_get": "persona show",
    "opal_persona_validate": "persona validate",
    "opal_persona_patch": "persona update",
    "opal_persona_delete": "persona remove",
    "generate_speech": "generate",
    "stream_speech": "generate --stream",
    "stream_speech_incremental": "generate --incremental",
    "opal_setting_list": "config setting list",
    "opal_setting_get": "config setting show",
    "opal_setting_put": "config setting set",
    "opal_setting_delete": "config setting remove",
    "opal_health_get": "service health",
    "opal_config_save": "config save",
    "opal_config_load": "config load",
    "opal_config_validate": "config validate",
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
    env_file: str = typer.Option(None, "--env-file", help="Load this dotenv file instead of ./.env"),
    no_env_file: bool = typer.Option(False, "--no-env-file", help="Disable dotenv loading and .env initialization"),
) -> None:
    from opal_say.config import dotenv_baseline
    from opal_say.dotenv import DotenvError, prepare

    try:
        prepare(env_file=env_file, no_env_file=no_env_file, baseline=dotenv_baseline())
    except DotenvError as exc:
        raise typer.BadParameter(str(exc)) from exc
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
