"""`opal say config ...` — save, load and validate portable configuration snapshots."""

from __future__ import annotations

import json
from pathlib import Path

import typer
import yaml

from opal_say.cli.commands.setting import config_app
from opal_say.cli.common import _call, _emit, read_document


@config_app.command("save")
def config_save(ctx: typer.Context, output: str = typer.Option(None, "--out")) -> None:
    path = Path(output) if output else None
    if path and path.suffix.lower() not in {".json", ".yaml", ".yml"}:
        raise typer.BadParameter("use a .json, .yaml or .yml output file")
    result = _call(ctx, "opal_config_save", json={"destination": "response" if path else "managed"}).json()
    if path:
        text = (json.dumps(result["snapshot"], indent=2, ensure_ascii=False) if path.suffix.lower() == ".json"
                else yaml.safe_dump(result["snapshot"], sort_keys=False, allow_unicode=True))
        try:
            path.write_text(text + "\n", encoding="utf-8")
        except OSError as exc:
            raise typer.BadParameter(f"cannot write snapshot: {exc}") from None
    _emit(ctx, result)


@config_app.command("load")
def config_load(ctx: typer.Context, input_file: str = typer.Option(None, "--in")) -> None:
    body = {"snapshot": read_document(input_file)} if input_file else {}
    _emit(ctx, _call(ctx, "opal_config_load", json=body).json())


@config_app.command("validate")
def config_validate(ctx: typer.Context, input_file: str = typer.Option(None, "--in")) -> None:
    body = {"snapshot": read_document(input_file)} if input_file else {}
    report = _call(ctx, "opal_config_validate", json=body).json()
    _emit(ctx, report)
    if not report["valid"]:
        raise typer.Exit(1)


def register(say_app: typer.Typer) -> None:
    pass
