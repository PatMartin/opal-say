"""SQLite persistence for engine configuration, voice registrations, profiles and settings."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS engines (
    engine_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL,
    options TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS voices (
    voice_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    engine_id TEXT NOT NULL,
    engine_voice_ref TEXT NOT NULL,
    language TEXT,
    enabled INTEGER NOT NULL,
    native_sample_rate INTEGER,
    engine_options TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    is_active INTEGER NOT NULL,
    shared TEXT NOT NULL,
    modules TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS personas (
    persona_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    voice_id TEXT,
    enabled INTEGER NOT NULL,
    origin TEXT NOT NULL DEFAULT 'local',
    owner_etag TEXT,
    owner_checked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS persona_cache (
    persona_id TEXT PRIMARY KEY,
    document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

JSON_COLUMNS = {"options", "engine_options", "metadata", "shared", "modules"}
BOOL_COLUMNS = {"enabled", "is_active"}


def now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in dict(row).items():
        if key in JSON_COLUMNS:
            value = json.loads(value)
        elif key in BOOL_COLUMNS:
            value = bool(value)
        out[key] = value
    return out


def _encode(values: dict[str, Any]) -> dict[str, Any]:
    return {k: json.dumps(v) if k in JSON_COLUMNS else (int(v) if k in BOOL_COLUMNS else v) for k, v in values.items()}


class Store:
    """Thread-safe thin repository over a single SQLite connection."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        if str(db_path) != ":memory:":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.created = str(db_path) == ":memory:" or not self.db_path.exists()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        self._conn.executescript(SCHEMA)
        try:
            self._migrate_profiles()
        except BaseException:
            self._conn.close()
            raise
        if "origin" not in {r[1] for r in self._conn.execute("PRAGMA table_info(personas)")}:
            self._conn.execute("ALTER TABLE personas ADD COLUMN origin TEXT NOT NULL DEFAULT 'local'")
        existing = {r[1] for r in self._conn.execute("PRAGMA table_info(personas)")}
        for col in ("owner_etag", "owner_checked_at"):
            if col not in existing:
                self._conn.execute(f"ALTER TABLE personas ADD COLUMN {col} TEXT")
        self._migrate_personas()

    def _migrate_personas(self) -> None:
        if not any(r[1] == "voice_id" and r[3] for r in self._conn.execute("PRAGMA table_info(personas)")):
            return
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute("CREATE TABLE personas_next (persona_id TEXT PRIMARY KEY, name TEXT NOT NULL, "
                               "description TEXT, voice_id TEXT, enabled INTEGER NOT NULL, origin TEXT NOT NULL DEFAULT 'local', "
                               "owner_etag TEXT, owner_checked_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            self._conn.execute("INSERT INTO personas_next SELECT persona_id, name, description, voice_id, enabled, origin, "
                               "owner_etag, owner_checked_at, created_at, updated_at FROM personas")
            self._conn.execute("DROP TABLE personas")
            self._conn.execute("ALTER TABLE personas_next RENAME TO personas")
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            self._conn.close()
            raise

    def list_owner_personas(self) -> list[dict[str, Any]]:
        return [json.loads(row["document"]) for row in self._all("SELECT document FROM persona_cache ORDER BY persona_id")]

    def get_owner_persona(self, persona_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT document FROM persona_cache WHERE persona_id = ?", (persona_id,))
        return json.loads(row["document"]) if row else None

    def cache_owner_persona(self, persona_id: str, document: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO persona_cache VALUES (?, ?) ON CONFLICT(persona_id) "
                               "DO UPDATE SET document=excluded.document", (persona_id, json.dumps(document)))

    def _migrate_profiles(self) -> None:
        if "modules" in {r[1] for r in self._conn.execute("PRAGMA table_info(profiles)")}:
            return
        from opal_say.schemas import validate_profile_document

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute("CREATE TABLE profiles_next (id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
                               "description TEXT, is_active INTEGER NOT NULL, shared TEXT NOT NULL, "
                               "modules TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            for raw in self._conn.execute("SELECT * FROM profiles").fetchall():
                old = _decode(raw)
                own = {k: old[k] for k in ("voice_id", "speed", "volume", "output_format", "sample_rate", "engine_options")}
                row = {k: old[k] for k in ("name", "description", "is_active", "created_at", "updated_at")}
                row.update(id=str(old["id"]), shared={}, modules={"opal-say": own})
                validate_profile_document(row, "OpalProfile")
                self._insert(self._conn, "profiles_next", row)
            self._conn.execute("DROP TABLE profiles")
            self._conn.execute("ALTER TABLE profiles_next RENAME TO profiles")
            self._conn.execute("COMMIT")
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Write transaction. A commit that actually modified rows bumps `config_revision`
        (the store-wide mutation counter backing ETag/If-Match in contract 1.4.0)."""
        with self._lock:
            changes_before = self._conn.total_changes
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            if self._conn.total_changes != changes_before:
                self._conn.execute(
                    "INSERT INTO meta (key, value) VALUES ('config_revision', '1') "
                    "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1"
                )
            self._conn.execute("COMMIT")

    @contextmanager
    def write_lock(self) -> Iterator[None]:
        """Hold the write lock across a revision check plus its mutation.

        Every write goes through `transaction()` on this same lock, so holding it
        makes a check-and-mutate atomic against other writers (TOCTOU guard).
        """
        with self._lock:
            yield

    def config_revision(self) -> int:
        """Monotonic counter bumped by every committed write transaction (0 if none yet)."""
        row = self._one("SELECT value FROM meta WHERE key = 'config_revision'")
        return int(row["value"]) if row else 0

    def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [_decode(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    @staticmethod
    def _insert(conn: sqlite3.Connection, table: str, values: dict[str, Any]) -> int:
        enc = _encode(values)
        cols = ", ".join(enc)
        marks = ", ".join("?" for _ in enc)
        return conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(enc.values())).lastrowid

    @staticmethod
    def _update(conn: sqlite3.Connection, table: str, key_col: str, key: Any, values: dict[str, Any]) -> None:
        enc = _encode(values)
        assignments = ", ".join(f"{c} = ?" for c in enc)
        conn.execute(f"UPDATE {table} SET {assignments} WHERE {key_col} = ?", (*enc.values(), key))

    # -- engines ------------------------------------------------------------

    def get_engine_state(self, engine_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM engines WHERE engine_id = ?", (engine_id,))

    def list_engine_states(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM engines ORDER BY engine_id")

    def upsert_engine_state(self, engine_id: str, enabled: bool, options: dict[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO engines (engine_id, enabled, options) VALUES (?, ?, ?) "
                "ON CONFLICT(engine_id) DO UPDATE SET enabled = excluded.enabled, options = excluded.options",
                (engine_id, int(enabled), json.dumps(options)),
            )

    # -- voices -------------------------------------------------------------

    def list_voices(self, engine_id: str | None = None, enabled: bool | None = None,
                    language: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM voices WHERE 1 = 1", []
        if engine_id is not None:
            sql += " AND engine_id = ?"
            params.append(engine_id)
        if enabled is not None:
            sql += " AND enabled = ?"
            params.append(int(enabled))
        rows = self._all(sql + " ORDER BY voice_id", tuple(params))
        if language is not None:
            want = language.lower().replace("-", "_")
            rows = [r for r in rows if (r["language"] or "").lower().replace("-", "_").startswith(want)]
        return rows

    def get_voice(self, voice_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM voices WHERE voice_id = ?", (voice_id,))

    def insert_voice(self, values: dict[str, Any]) -> None:
        with self.transaction() as conn:
            self._insert(conn, "voices", values)

    def update_voice(self, voice_id: str, values: dict[str, Any]) -> None:
        with self.transaction() as conn:
            self._update(conn, "voices", "voice_id", voice_id, values)

    def delete_voice(self, voice_id: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM voices WHERE voice_id = ?", (voice_id,))

    # -- profiles -------------------------------------------------------------

    def list_profiles(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM profiles ORDER BY id")

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM profiles WHERE id = ?", (profile_id,))

    def get_profile_by_name(self, name: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM profiles WHERE name = ?", (name,))

    def active_profile(self) -> dict[str, Any] | None:
        return self._one("SELECT * FROM profiles WHERE is_active = 1 ORDER BY id LIMIT 1")

    def profiles_using_voice(self, voice_id: str) -> list[dict[str, Any]]:
        return [p for p in self.list_profiles() if p["modules"].get("opal-say", {}).get("voice_id") == voice_id]

    def insert_profile(self, values: dict[str, Any]) -> str:
        values = {**values, "id": values.get("id") or uuid.uuid4().hex}
        with self.transaction() as conn:
            if values.get("is_active"):
                conn.execute("UPDATE profiles SET is_active = 0")
            self._insert(conn, "profiles", values)
        return values["id"]

    def update_profile(self, profile_id: str, values: dict[str, Any]) -> None:
        with self.transaction() as conn:
            if values.get("is_active"):
                conn.execute("UPDATE profiles SET is_active = 0 WHERE id != ?", (profile_id,))
            self._update(conn, "profiles", "id", profile_id, values)

    def delete_profile(self, profile_id: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))

    # -- personas -------------------------------------------------------------

    def list_personas(self, enabled: bool | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM personas WHERE 1 = 1", []
        if enabled is not None:
            sql += " AND enabled = ?"
            params.append(int(enabled))
        return self._all(sql + " ORDER BY persona_id", tuple(params))

    def get_persona(self, persona_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM personas WHERE persona_id = ?", (persona_id,))

    def personas_using_voice(self, voice_id: str) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM personas WHERE voice_id = ? ORDER BY persona_id", (voice_id,))

    def insert_persona(self, values: dict[str, Any]) -> None:
        with self.transaction() as conn:
            self._insert(conn, "personas", values)

    def update_persona(self, persona_id: str, values: dict[str, Any]) -> None:
        with self.transaction() as conn:
            self._update(conn, "personas", "persona_id", persona_id, values)

    def delete_persona(self, persona_id: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM personas WHERE persona_id = ?", (persona_id,))

    # -- settings -----------------------------------------------------------

    def list_settings(self) -> list[dict[str, Any]]:
        return self._all("SELECT * FROM settings ORDER BY key")

    def get_setting(self, key: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM settings WHERE key = ?", (key,))

    def upsert_setting(self, key: str, value: str) -> dict[str, Any]:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, now_iso()),
            )
        return self.get_setting(key)  # type: ignore[return-value]

    def delete_setting(self, key: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))

    def patch_settings(self, changes: dict[str, str | None]) -> None:
        """Merge-patch settings atomically: non-null values upserted, `null` deletes."""
        stamp = now_iso()
        with self.transaction() as conn:
            for key, value in changes.items():
                if value is None:
                    conn.execute("DELETE FROM settings WHERE key = ?", (key,))
                else:
                    conn.execute(
                        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                        "updated_at = excluded.updated_at",
                        (key, value, stamp),
                    )

    # -- bulk replace (import/restore) -----------------------------------------

    def replace_all(
        self,
        engines: dict[str, dict[str, Any]] | None,
        voices: list[dict[str, Any]] | None,
        profiles: list[dict[str, Any]] | None,
        settings: dict[str, str] | None,
    ) -> None:
        """Atomically apply each provided section; `None` leaves a section untouched.

        Voices, profiles and settings are fully replaced (deleted then re-inserted);
        engine entries are merged — upserted `enabled`/`options` for installed engines,
        state for unlisted engines is preserved.
        """
        stamp = now_iso()
        with self.transaction() as conn:
            if engines is not None:
                for engine_id, state in engines.items():
                    conn.execute(
                        "INSERT INTO engines (engine_id, enabled, options) VALUES (?, ?, ?) "
                        "ON CONFLICT(engine_id) DO UPDATE SET enabled = excluded.enabled, options = excluded.options",
                        (engine_id, int(state["enabled"]), json.dumps(state["options"])),
                    )
            if profiles is not None:
                conn.execute("DELETE FROM profiles")
            if voices is not None:
                conn.execute("DELETE FROM voices")
                for voice in voices:
                    self._insert(conn, "voices", {"created_at": stamp, "updated_at": stamp, **voice})
            if profiles is not None:
                for profile in profiles:
                    self._insert(conn, "profiles", {"created_at": stamp, "updated_at": stamp, **profile})
            if settings is not None:
                conn.execute("DELETE FROM settings")
                for key, value in settings.items():
                    conn.execute(
                        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)", (key, value, stamp)
                    )
