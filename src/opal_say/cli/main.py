"""`opal say ...` command line. Every command calls the OPAL Say REST API (see COMMANDS)."""

from __future__ import annotations

import click
import typer
from typer.core import TyperGroup

from opal_say.cli.client import api_url_from_env
from opal_say.cli.commands import backup, engines, generate, profiles, root, settings, voices
from opal_say.cli.common import CliState, client_factory  # noqa: F401 - re-exported; tests patch main.client_factory


class SayGroup(TyperGroup):
    """`opal say TEXT` speaks text aloud; unknown arguments route to the hidden `speak` command."""

    def resolve_command(self, ctx: click.Context, args: list[str]) -> tuple[str | None, click.Command | None, list[str]]:
        if args and args[0] not in self.commands and (args[0] == "-" or not args[0].startswith("-")):
            return "speak", self.commands["speak"], args
        return super().resolve_command(ctx, args)

# REST operationId -> CLI command (relative to `opal say`). Verified by the parity tests.
COMMANDS: dict[str, str] = {
    "opal_engines_list": "engines list",
    "opal_engine_get": "engines get",
    "opal_engine_patch": "engines update",
    "validate_engine_api_v1_engines__engine_id__validate_post": "engines validate",
    "list_voices_api_v1_voices__get": "voices list",
    "create_voice_api_v1_voices__post": "voices add",
    "get_voice_api_v1_voices__voice_id__get": "voices get",
    "update_voice_api_v1_voices__voice_id__patch": "voices update",
    "delete_voice_api_v1_voices__voice_id__delete": "voices remove",
    "validate_voice_api_v1_voices__voice_id__validate_post": "voices validate",
    "test_voice_api_v1_voices__voice_id__test_post": "voices test",
    "opal_profiles_list": "profiles list",
    "opal_profiles_create": "profiles add",
    "opal_profile_get": "profiles get",
    "opal_profile_patch": "profiles update",
    "opal_profile_delete": "profiles remove",
    "opal_profile_activate": "profiles activate",
    "generate_audio_api_v1_generate_post": "generate",
    "stream_audio_api_v1_generate_stream_post": "generate --stream",
    "opal_settings_get": "settings list",
    "opal_setting_get": "settings get",
    "opal_setting_put": "settings set",
    "opal_setting_delete": "settings delete",
    "opal_health_get": "health",
    "opal_health_live_get": "health --live",
    "opal_health_ready_get": "health --ready",
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
    "root__get": "info",
    "healthz_healthz_get": "ping",
}


say_app = typer.Typer(
    cls=SayGroup,
    help="OPAL Say text-to-speech (talks to the opal-say-svc REST API). 'opal say TEXT' speaks aloud.",
    epilog="Global options (--api-url, --json, --timeout) go before the command group, "
           "e.g. 'opal say --json voices list'.",
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


for _module in (engines, voices, profiles, generate, settings, backup, root):
    _module.register(say_app)


# -- `opal` root -----------------------------------------------------------------

app = typer.Typer(help="OPAL command line.", no_args_is_help=True, pretty_exceptions_enable=False)
app.add_typer(say_app, name="say", cls=SayGroup)

try:  # Share the `opal` root with the sister OPAL Listen CLI when it is installed.
    from opal_listener.cli.main import listen_app  # type: ignore[import-not-found]

    app.add_typer(listen_app, name="listen")
except Exception:  # noqa: BLE001
    pass


if __name__ == "__main__":
    app()
