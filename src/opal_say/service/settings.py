"""Service-wide key/value settings. Known keys are validated; other keys are stored verbatim."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from opal_say.persistence.store import Store
from opal_say.service.errors import InvalidRequest, NotFound


def _number(kind: type, lo: float, hi: float) -> Callable[[str], Any]:
    def parse(raw: str) -> Any:
        try:
            value = kind(raw)
        except ValueError:
            raise ValueError(f"must be a {kind.__name__}") from None
        if not lo <= value <= hi:
            raise ValueError(f"must be between {lo} and {hi}")
        return value

    return parse


def _choice(*choices: str) -> Callable[[str], str]:
    def parse(raw: str) -> str:
        if raw not in choices:
            raise ValueError(f"must be one of {list(choices)}")
        return raw

    return parse


# key -> (parser, default). Defaults apply when the key is not stored.
KNOWN_SETTINGS: dict[str, tuple[Callable[[str], Any], Any]] = {
    "max_text_chars": (_number(int, 1, 1_000_000), 20_000),
    "max_audio_bytes": (_number(int, 1 << 16, 1 << 29), 128 << 20),
    "chunk_max_chars": (_number(int, 50, 5_000), 400),
    "default_speed": (_number(float, 0.25, 4.0), 1.0),
    "default_volume": (_number(float, 0.0, 2.0), 1.0),
    "default_output_format": (_choice("wav", "pcm"), "wav"),
    # Max wait for a pending engine reconfiguration or an inference permit before
    # generation admission fails (also bounds each permit wait inside a stream).
    "engine_acquire_timeout_ms": (_number(int, 100, 600_000), 30_000),
    # Streaming output: bounded producer buffer (blocks) and how long a full
    # buffer may sit undrained before the stream is abandoned to free the engine.
    "stream_buffer_blocks": (_number(int, 1, 64), 8),
    "stream_lag_timeout_ms": (_number(int, 500, 300_000), 30_000),
    # Incremental input: text chunks accepted ahead of synthesis. A full buffer
    # stops request-body reads, pushing backpressure onto the producer.
    "incremental_pending_chunks": (_number(int, 1, 64), 8),
    # Owner-synced personas: how long a cached owner record may serve before it
    # is revalidated against the persona owner. 0 revalidates on every use.
    "persona_refresh_seconds": (_number(int, 0, 86_400), 60),
}


def settings_to_str(raw: dict[str, Any]) -> dict[str, str]:
    """Normalize native JSON setting values to storage strings (shared by PATCH,
    YAML import and backup restore).

    Known keys store the canonical scalar string their parser validates (a
    parseable string stays accepted). Unknown keys store the JSON encoding of
    the value so arbitrary typed values round-trip faithfully.
    """
    out: dict[str, str] = {}
    for key, value in raw.items():
        k = str(key)
        if value is None:
            out[k] = ""
        elif k in KNOWN_SETTINGS:
            out[k] = str(value)
        else:
            out[k] = json.dumps(value)
    return out


def setting_typed_value(key: str, raw: str) -> Any:
    """Rehydrate a stored value for the API: parsers for known keys, JSON decoding
    for unknown keys (legacy raw strings pass through unchanged)."""
    if key in KNOWN_SETTINGS:
        try:
            return KNOWN_SETTINGS[key][0](raw)
        except ValueError:
            return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def setting_problem(key: str, value: str) -> str | None:
    if key not in KNOWN_SETTINGS:
        return "is not a registered setting"
    try:
        KNOWN_SETTINGS[key][0](value)
    except ValueError as exc:
        return str(exc)
    return None


class SettingsService:
    def __init__(self, store: Store):
        self.store = store

    def all(self) -> dict[str, Any]:
        return {key: self.effective(key) for key in sorted(KNOWN_SETTINGS)}

    def get(self, key: str) -> dict[str, Any]:
        if key not in KNOWN_SETTINGS:
            raise NotFound(f"setting '{key}' is not registered", details={"key": key})
        row = self.store.get_setting(key)
        default = KNOWN_SETTINGS[key][1]
        configured = setting_typed_value(key, row["value"]) if row else default
        env_key = f"OPAL_SAY_{key.upper()}"
        source = "persisted" if row else "default"
        warnings = []
        if env_key in os.environ:
            from opal_say.dotenv import DOTENV_VALUES

            source = "dotenv" if DOTENV_VALUES.get(env_key) == os.environ[env_key] else "environment"
            if row:
                warnings.append({"code": "masked_override", "message": "environment overlay masks the persistent override",
                                 "field": key})
        return {**(row or {}), "key": key, "value": self.effective(key), "configured_value": configured,
                "default": default, "has_override": row is not None, "is_default": source == "default",
                "source": source, "restart_required": False, "pending_restart": False, "warnings": warnings}

    def upsert(self, key: str, value: Any) -> dict[str, Any]:
        self.get(key)
        text = settings_to_str({key: value})[key]
        problem = setting_problem(key, text)
        if problem:
            raise InvalidRequest.single(["body", "value"], f"setting '{key}' {problem}", input_=value)
        self.store.upsert_setting(key, text)
        return self.get(key)

    def delete(self, key: str) -> None:
        self.get(key)
        self.store.delete_setting(key)

    def patch(self, changes: dict[str, Any]) -> dict[str, str]:
        """Merge-patch the whole settings document: non-null values set, `null` deletes.

        Values are normalized to strings (same rules as YAML import); known keys are
        validated. Every change is checked first, then applied in one transaction —
        all or nothing — and the full resulting map is returned. Contract 1.4.0's
        `PATCH /api/v1/settings/` sits directly on top of this.
        """
        normalized: dict[str, str | None] = {}
        errors: list[dict[str, Any]] = []
        for key, value in changes.items():
            if value is None:
                normalized[str(key)] = None
                continue
            text = settings_to_str({key: value})[key]
            problem = setting_problem(str(key), text)
            if problem:
                errors.append(
                    {"loc": ["body", str(key)], "msg": f"setting '{key}' {problem}",
                     "type": "value_error", "input": value}
                )
            else:
                normalized[str(key)] = text
        if errors:
            raise InvalidRequest(errors)
        self.store.patch_settings(normalized)
        return self.all()

    def effective(self, key: str) -> Any:
        parser, default = KNOWN_SETTINGS[key]
        row = self.store.get_setting(key)
        raw = os.environ.get(f"OPAL_SAY_{key.upper()}", row["value"] if row else str(default))
        try:
            return parser(raw)
        except ValueError:
            return parser(row["value"]) if row else default
