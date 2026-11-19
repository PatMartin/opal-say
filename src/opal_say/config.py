"""Service (process) configuration loaded from YAML with environment overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_FILE = Path("config/service.yaml")


@dataclass
class ServiceConfig:
    host: str = "127.0.0.1"
    port: int = 8001
    data_dir: Path = Path("var")
    config_dir: Path = Path("config")
    log_level: str = "info"
    persona_owner_url: str | None = None
    persona_owner_token_env: str = "OPAL_SAY_PERSONA_OWNER_TOKEN"
    auth_enabled: bool = False
    token_env: str = "OPAL_SAY_TOKEN"
    admin_token_env: str = "OPAL_SAY_ADMIN_TOKEN"
    cors_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    spa_dir: Path | None = None
    log_dir: Path = Path("logs")  # managed server log (opal_say.log) and pidfile (opal_say.pid)
    # The YAML file this config was loaded from, when any; propagated to managed children.
    source_path: Path | None = None
    # engine_id -> {"enabled": bool, "options": {...}}; used to seed engine state on first start.
    engines: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "opal_say.db"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"


def load_config(path: Path | str | None = None, *, apply_env: bool = True) -> ServiceConfig:
    """Load YAML config from `path`, $OPAL_SAY_CONFIG or ./config/service.yaml (if present)."""
    if path is None:
        env = os.environ.get("OPAL_SAY_CONFIG") if apply_env else None
        path = Path(env) if env else (DEFAULT_CONFIG_FILE if DEFAULT_CONFIG_FILE.is_file() else None)
    raw: dict[str, Any] = {}
    if path is not None:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"service config '{path}' must be a YAML mapping")
    raw = raw.get("service", raw)

    cfg = ServiceConfig()
    for key in ("host", "log_level"):
        if key in raw:
            setattr(cfg, key, str(raw[key]))
    if "port" in raw:
        cfg.port = int(raw["port"])
    if "auth_enabled" in raw:
        if not isinstance(raw["auth_enabled"], bool):
            raise ValueError("auth_enabled must be a boolean")
        cfg.auth_enabled = raw["auth_enabled"]
    for key in ("token_env", "admin_token_env"):
        if key in raw:
            setattr(cfg, key, str(raw[key]))
    for key in ("data_dir", "config_dir", "spa_dir"):
        if raw.get(key):
            setattr(cfg, key, Path(str(raw[key])).expanduser())
    if raw.get("persona_owner_url"):
        cfg.persona_owner_url = str(raw["persona_owner_url"]).rstrip("/")
    if raw.get("persona_owner_token_env"):
        cfg.persona_owner_token_env = str(raw["persona_owner_token_env"])
    if "cors_origins" in raw:
        cfg.cors_origins = [str(o) for o in raw["cors_origins"] or []]
    if isinstance(raw.get("engines"), dict):
        cfg.engines = {str(k): dict(v or {}) for k, v in raw["engines"].items()}
    if path is not None:
        cfg.source_path = Path(path).resolve()

    env_map = {
        "OPAL_SAY_HOST": ("host", str),
        "OPAL_SAY_PORT": ("port", int),
        "OPAL_SAY_DATA_DIR": ("data_dir", lambda v: Path(v).expanduser()),
        "OPAL_SAY_CONFIG_DIR": ("config_dir", lambda v: Path(v).expanduser()),
        "OPAL_SAY_SPA_DIR": ("spa_dir", lambda v: Path(v).expanduser()),
        "OPAL_SAY_LOG_DIR": ("log_dir", lambda v: Path(v).expanduser()),
        "OPAL_SAY_PERSONA_OWNER_URL": ("persona_owner_url", str),
        "OPAL_SAY_PERSONA_OWNER_TOKEN_ENV": ("persona_owner_token_env", str),
    }
    if apply_env:
        for env_key, (attr, conv) in env_map.items():
            if env_key in os.environ:
                setattr(cfg, attr, conv(os.environ[env_key]))
    return cfg


def dotenv_baseline(path: Path | str | None = None) -> dict[str, object]:
    cfg = load_config(path, apply_env=False)
    values = {
        "OPAL_SAY_HOST": cfg.host,
        "OPAL_SAY_PORT": cfg.port,
        "OPAL_SAY_DATA_DIR": cfg.data_dir,
        "OPAL_SAY_CONFIG_DIR": cfg.config_dir,
        "OPAL_SAY_SPA_DIR": cfg.spa_dir or "",
        "OPAL_SAY_LOG_DIR": cfg.log_dir,
        "OPAL_SAY_PERSONA_OWNER_URL": cfg.persona_owner_url or "",
    }
    if path is not None or DEFAULT_CONFIG_FILE.is_file():
        values["OPAL_SAY_CONFIG"] = str(path or DEFAULT_CONFIG_FILE)
    return values
