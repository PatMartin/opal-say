"""Configuration export/import/validation and backup/restore — facade.

The work lives in two cohesive units: `CandidateService` (candidate configuration
pipeline in `config_candidate`) and `BackupService` (`.tar.gz` archive I/O in
`backups`). This class keeps the historical `ConfigService` API surface stable.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from opal_say.contract import load_contract
from opal_say.persistence.store import Store, now_iso
from opal_say.schemas import PersonaCreate, VoiceCreate
from opal_say.service.backups import BackupService
from opal_say.service.catalog import EngineService
from opal_say.service.concurrency import config_etag
from opal_say.service.config_candidate import Candidate, CandidateService
from opal_say.service.errors import InvalidRequest, NotFound, OperationFailed
from opal_say.service.settings import KNOWN_SETTINGS, setting_problem, setting_typed_value, settings_to_str
from opal_say.tts.base import Report
from opal_say.tts.runtime import split_runtime_options

CONFIG_FILES = ("engines.yaml", "voices.yaml", "profiles.yaml", "settings.yaml")


class ConfigService:
    def __init__(self, store: Store, engines: EngineService, config_dir: Path, backup_dir: Path):
        self.store = store
        self.engines = engines
        self.snapshot_path = backup_dir.parent / "config-snapshot.json"
        self.candidates = CandidateService(store, engines, config_dir)
        self.backups = BackupService(self.candidates, backup_dir)

    # -- candidate pipeline ---------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        voice_fields = {"voice_id", "name", "description", "engine_id", "engine_voice_ref", "language",
                        "enabled", "engine_options"}
        persona_fields = {"persona_id", "name", "description", "voice_id", "enabled", "origin", "created_at", "updated_at"}
        return {
            "format_version": "1.0.0", "module": "opal-say", "created_at": now_iso(),
            "settings": {r["key"]: setting_typed_value(r["key"], r["value"]) for r in self.store.list_settings()},
            "resources": {
                "profiles": self.store.list_profiles(),
                "personas": [{k: v for k, v in p.items() if k in persona_fields} for p in self.store.list_personas()],
                "voices": [{k: v for k, v in voice.items() if k in voice_fields} for voice in self.store.list_voices()],
                "engines": [{k: row[k] for k in ("engine_id", "enabled", "options")} for row in self.store.list_engine_states()],
            },
        }

    def validate_snapshot(self, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        candidate = self.snapshot() if snapshot is None else snapshot
        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []

        def issue(bucket, code, path, message):
            bucket.append({"code": code, "path": path, "message": message})

        def schema(value, name, prefix):
            validator = Draft202012Validator({"$ref": f"#/components/schemas/{name}",
                                              "components": load_contract()["components"]}, format_checker=FormatChecker())
            found = list(validator.iter_errors(value))
            for error in found:
                path = ".".join([prefix, *map(str, error.absolute_path)]).strip(".")
                issue(errors, "schema_invalid", path, error.message)
            return not found

        if not schema(candidate, "OpalConfigSnapshot", ""):
            return {"valid": False, "errors": errors, "warnings": warnings}
        if candidate["format_version"] != "1.0.0":
            issue(errors, "unsupported_version", "format_version", "only snapshot format 1.0.0 is supported")
        if candidate["module"] != "opal-say":
            issue(errors, "wrong_module", "module", "snapshot must belong to opal-say")
        resources = candidate["resources"]
        for key in resources.keys() - {"profiles", "personas", "voices", "engines"}:
            issue(errors, "unknown_section", f"resources.{key}", "unknown resource section")
        for key, value in candidate["settings"].items():
            if key not in KNOWN_SETTINGS:
                issue(errors, "unknown_setting", f"settings.{key}", "setting is not registered")
            elif problem := setting_problem(key, settings_to_str({key: value})[key]):
                issue(errors, "invalid_setting", f"settings.{key}", problem)

        def check_secrets(node, path):
            if isinstance(node, dict):
                for key, value in node.items():
                    if re.fullmatch(r"(?:api[_-]?key|access[_-]?token|token|secret|password|credential)s?", key, re.I):
                        issue(errors, "secret_value", f"{path}.{key}",
                              "use a credential environment-variable reference, not a secret value")
                    else:
                        check_secrets(value, f"{path}.{key}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    check_secrets(value, f"{path}.{index}")

        check_secrets(resources, "resources")
        parsed: dict[str, list[dict[str, Any]]] = {}
        names = {"voices": "VoiceCreate", "profiles": "OpalProfile", "personas": "OpalPersonaRead", "engines": None}
        id_fields = {"voices": "voice_id", "profiles": "id", "personas": "persona_id", "engines": "engine_id"}
        for section, schema_name in names.items():
            rows = resources.get(section, [])
            parsed[section] = []
            if not isinstance(rows, list):
                issue(errors, "schema_invalid", f"resources.{section}", "resource collection must be an array")
                continue
            seen = set()
            for index, row in enumerate(rows):
                path = f"resources.{section}.{index}"
                if section == "engines":
                    valid = (isinstance(row, dict) and set(row) <= {"engine_id", "enabled", "options"}
                             and isinstance(row.get("engine_id"), str) and bool(row["engine_id"])
                             and isinstance(row.get("enabled"), bool) and isinstance(row.get("options"), dict))
                    if not valid:
                        issue(errors, "schema_invalid", path, "engine configuration requires engine_id, enabled and options only")
                        continue
                elif not schema(row, schema_name, path):
                    continue
                identifier = row[id_fields[section]]
                if identifier in seen:
                    issue(errors, "duplicate_resource", path, "resource identifiers must be unique")
                seen.add(identifier)
                parsed[section].append(row)
        engines = {row["engine_id"]: row for row in parsed["engines"]}
        for engine_id, row in engines.items():
            engine = self.engines.runtime.get(engine_id)
            if engine is None:
                issue(warnings, "engine_not_installed", f"resources.engines.{engine_id}",
                      "configuration retained for an unavailable adapter")
                continue
            concurrency = row["options"].get("max_concurrent_inferences")
            if concurrency is not None and (type(concurrency) is not int or concurrency < 1 or concurrency > 64):
                issue(errors, "invalid_option", f"resources.engines.{engine_id}.options.max_concurrent_inferences",
                      "must be an integer between 1 and 64")
                continue
            for error in engine.validate_options(split_runtime_options(row["options"])[0], "engine"):
                issue(errors, error.code, f"resources.engines.{engine_id}.options.{error.field}", error.message)
        voices = {row["voice_id"]: row for row in parsed["voices"]}
        for voice_id, row in voices.items():
            engine = self.engines.runtime.get(row["engine_id"])
            if row["engine_id"] not in engines and engine is None:
                issue(errors, "engine_not_found", f"resources.voices.{voice_id}.engine_id", "voice references an unknown engine")
            if engine:
                for error in engine.validate_options(row.get("engine_options", {}), "voice"):
                    issue(errors, error.code, f"resources.voices.{voice_id}.engine_options.{error.field}", error.message)
        profile_names = set()
        active = 0
        for row in parsed["profiles"]:
            path = f"resources.profiles.{row['id']}"
            if row["name"] in profile_names:
                issue(errors, "duplicate_profile", path, "profile names must be unique")
            profile_names.add(row["name"])
            active += bool(row.get("is_active"))
            own = row.get("modules", {}).get("opal-say", {})
            for namespace in row.get("modules", {}).keys() - {"opal-say", "opal-listen", "opal-think"}:
                issue(warnings, "unknown_namespace", f"{path}.modules.{namespace}",
                      "foreign compartment preserved without interpretation")
            if own.get("voice_id") and own["voice_id"] not in voices:
                issue(errors, "voice_not_found", f"{path}.modules.opal-say.voice_id", "profile references a missing voice")
            voice = voices.get(own.get("voice_id"))
            engine = self.engines.runtime.get(voice["engine_id"]) if voice else None
            if engine:
                for error in engine.validate_options(own.get("engine_options", {}), "generation"):
                    issue(errors, error.code, f"{path}.modules.opal-say.engine_options.{error.field}", error.message)
            if own.get("output_format", "wav") not in {"wav", "pcm"}:
                issue(errors, "invalid_format", f"{path}.modules.opal-say.output_format", "must be wav or pcm")
        if active > 1:
            issue(errors, "multiple_active_profiles", "resources.profiles", "at most one profile may be active")
        for row in parsed["personas"]:
            if row.get("voice_id") and row["voice_id"] not in voices and row.get("origin", "local") != "owner":
                issue(errors, "voice_not_found", f"resources.personas.{row['persona_id']}.voice_id",
                      "persona references a missing voice")
        return {"valid": not errors, "errors": errors, "warnings": warnings}

    def save_snapshot(self, destination: str) -> tuple[dict[str, Any], str]:
        with self.store.write_lock():
            snapshot = self.snapshot()
            revision = config_etag(self.store)
            report = self.validate_snapshot(snapshot)
            if not report["valid"]:
                raise InvalidRequest([{"loc": ["body", "snapshot", error["path"]], "msg": error["message"],
                                      "type": error["code"]} for error in report["errors"]])
            if destination == "managed":
                temporary = None
                try:
                    self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.snapshot_path.parent,
                                                     prefix=".snapshot-", suffix=".tmp", delete=False) as stream:
                        temporary = Path(stream.name)
                        json.dump(snapshot, stream, ensure_ascii=False, indent=2)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temporary, self.snapshot_path)
                except OSError as exc:
                    raise OperationFailed("managed snapshot could not be written", "snapshot_write_failed") from exc
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            return {"snapshot": snapshot, "managed": destination == "managed"}, revision

    def load_snapshot(self, snapshot: dict[str, Any] | None) -> dict[str, Any]:
        if snapshot is None:
            if not self.snapshot_path.is_file():
                raise NotFound("no managed snapshot exists", "snapshot_not_found")
            try:
                snapshot = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise OperationFailed("managed snapshot cannot be read", "snapshot_read_failed") from exc
        report = self.validate_snapshot(snapshot)
        if not report["valid"]:
            raise InvalidRequest([{"loc": ["body", "snapshot", error["path"]], "msg": error["message"],
                                      "type": error["code"]} for error in report["errors"]])
        resources = snapshot["resources"]
        engines = {row["engine_id"]: row for row in resources.get("engines", [])}
        try:
            prepared = self.engines.runtime.prepare_configurations(engines)
            with self.engines.runtime.configuration_batch(prepared), self.store.transaction() as connection:
                for table in ("settings", "profiles", "personas", "voices", "engines"):
                    connection.execute(f"DELETE FROM {table}")
                for key, value in settings_to_str(snapshot["settings"]).items():
                    self.store._insert(connection, "settings", {"key": key, "value": value, "updated_at": now_iso()})
                for row in engines.values():
                    self.store._insert(connection, "engines", row)
                for row in resources.get("voices", []):
                    values = VoiceCreate.model_validate(row).model_dump()
                    self.store._insert(connection, "voices", {"created_at": now_iso(), "updated_at": now_iso(),
                                       "native_sample_rate": None, "metadata": {}, **values})
                for row in resources.get("profiles", []):
                    values = {"description": None, "is_active": False, "shared": {}, "modules": {},
                              "created_at": now_iso(), "updated_at": now_iso(), **row}
                    self.store._insert(connection, "profiles", values)
                for row in resources.get("personas", []):
                    values = PersonaCreate.model_validate({k: v for k, v in row.items()
                                                          if k in PersonaCreate.model_fields}).model_dump()
                    self.store._insert(connection, "personas", {"created_at": row.get("created_at", now_iso()),
                                       "updated_at": row.get("updated_at", now_iso()),
                                       "origin": row.get("origin", "local"), **values})
        except Exception as exc:
            raise OperationFailed("snapshot application failed; previous configuration retained", "snapshot_load_failed") from exc
        return {"applied": True, "pending_restart_fields": [], "warnings": report["warnings"]}

    def export(self) -> dict[str, Any]:
        return self.candidates.export()

    def validate_candidate(self, cand: Candidate, resources_are_errors: bool) -> tuple[Report, dict[str, dict]]:
        return self.candidates.validate_candidate(cand, resources_are_errors)

    def validate_current(self) -> Report:
        return self.candidates.validate_current()

    def import_config(self) -> dict[str, Any]:
        return self.candidates.import_config()

    def import_bundle(self, dump: dict[str, Any]) -> dict[str, Any]:
        return self.candidates.import_bundle(dump)

    # -- backups ----------------------------------------------------------------

    def list_backups(self) -> list[dict[str, Any]]:
        return self.backups.list_backups()

    def create_backup(self) -> dict[str, Any]:
        return self.backups.create_backup()

    def backup_path(self, filename: str) -> Path:
        return self.backups.backup_path(filename)

    def delete_backup(self, filename: str) -> None:
        self.backups.delete_backup(filename)

    def restore_backup(self, filename: str) -> dict[str, Any]:
        return self.backups.restore_backup(filename)
