"""`opal say ...` command line. Every command calls the OPAL Say REST API (see COMMANDS)."""

from __future__ import annotations

import typer

from opal_say.cli.client import api_url_from_env
from opal_say.cli.commands import backup, engine, generate, persona, profile, root, settings, voice
from opal_say.cli.common import CliState, client_factory  # noqa: F401 - re-exported; tests patch main.client_factory

# REST operationId -> CLI command (relative to `opal say`). Verified by the parity tests.
COMMANDS: dict[str, str] = {
    "opal_engines_list": "engine list",
    "opal_engine_get": "engine get",
    "opal_engine_patch": "engine update",
    "validate_engine_api_v1_engine__engine_id__validate_post": "engine validate",
    "list_voice_api_v1_voice__get": "voice list",
    "create_voice_api_v1_voice__post": "voice add",
    "get_voice_api_v1_voice__voice_id__get": "voice get",
    "update_voice_api_v1_voice__voice_id__patch": "voice update",
    "delete_voice_api_v1_voice__voice_id__delete": "voice remove",
    "validate_voice_api_v1_voice__voice_id__validate_post": "voice validate",
    "test_voice_api_v1_voice__voice_id__test_post": "voice test",
    "opal_profiles_list": "profile list",
    "opal_profiles_create": "profile add",
    "opal_profile_get": "profile get",
    "opal_profile_patch": "profile update",
    "opal_profile_delete": "profile remove",
    "opal_profile_activate": "profile activate",
    "list_persona_api_v1_persona__get": "persona list",
    "create_persona_api_v1_persona__post": "persona add",
    "get_persona_api_v1_persona__persona_id__get": "persona get",
    "update_persona_api_v1_persona__persona_id__patch": "persona update",
    "delete_persona_api_v1_persona__persona_id__delete": "persona remove",
    "generate_audio_api_v1_generate_post": "generate",
    "stream_audio_api_v1_generate_stream_post": "generate --stream",
    "opal_settings_get": "settings list",
    "opal_setting_get": "settings get",
    "opal_setting_put": "settings set",
    "opal_setting_delete": "settings delete",
    "opal_health_get": "info health",
    "opal_health_live_get": "info live",
    "opal_health_ready_get": "info ready",
    "opal_settings_patch": "settings patch",
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


for _module in (engine, voice, profile, persona, generate, settings, backup, root):
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
