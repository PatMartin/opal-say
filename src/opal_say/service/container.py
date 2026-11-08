"""Service container and readiness reporting."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from opal_say import __version__
from opal_say.config import ServiceConfig
from opal_say.persistence.store import Store
from opal_say.service.catalog import EngineService, PersonaService, ProfileService, VoiceService
from opal_say.service.config_ops import ConfigService
from opal_say.service.errors import ApiError
from opal_say.service.generation import GenerationService
from opal_say.service.settings import SettingsService
from opal_say.tts.base import TtsEngine
from opal_say.tts.registry import EngineRegistry, default_engines
from opal_say.tts.runtime import EngineRuntimeManager

log = logging.getLogger(__name__)


@dataclass
class Services:
    config: ServiceConfig
    store: Store
    runtime: EngineRuntimeManager
    engine: EngineService
    voices: VoiceService
    profiles: ProfileService
    persona: PersonaService
    settings: SettingsService
    generation: GenerationService
    config_ops: ConfigService

    def health(self) -> dict[str, Any]:
        engine_states = []
        for engine in self.runtime.all():
            available, message = engine.availability()
            enabled = self.engine.state(engine.engine_id)["enabled"]
            engine_states.append({"engine_id": engine.engine_id, "enabled": enabled, "available": available,
                            "message": message})
        usable = [e for e in engine_states if e["enabled"] and e["available"]]
        broken = [e for e in engine_states if e["enabled"] and not e["available"]]
        status = "error" if not usable else ("degraded" if broken else "ok")
        active = self.store.active_profile()
        return {
            "status": status,
            "version": __version__,
            "active_profile_id": active["id"] if active else None,
            "engines": engine_states,
            "details": {
                "voices": len(self.store.list_voices()),
                "enabled_voices": len(self.store.list_voices(enabled=True)),
                "profiles": len(self.store.list_profiles()),
            },
        }

    def close(self) -> None:
        self.store.close()


def build_services(config: ServiceConfig, engine: list[TtsEngine] | None = None) -> Services:
    store = Store(config.db_path)
    fresh = store.created
    runtime = EngineRuntimeManager(EngineRegistry(engine if engine is not None else default_engines()))
    engine_svc = EngineService(store, runtime)
    engine_svc.bootstrap(config.engines)
    voice_svc = VoiceService(store, engine_svc)
    settings_svc = SettingsService(store)
    services = Services(
        config=config,
        store=store,
        runtime=runtime,
        engine=engine_svc,
        voices=voice_svc,
        profiles=ProfileService(store, engine_svc, voice_svc),
        persona=PersonaService(store, voice_svc),
        settings=settings_svc,
        generation=GenerationService(store, engine_svc, settings_svc),
        config_ops=ConfigService(store, engine_svc, config.config_dir, config.backup_dir),
    )
    if fresh and config.config_dir.is_dir():
        try:
            result = services.config_ops.import_config()
            if not result["success"]:
                log.warning("initial config import skipped: %s", [i.message for i in result["validation"].errors])
        except ApiError as exc:
            log.info("no initial configuration imported: %s", exc.message)
    return services
