"""Service container and readiness reporting."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from opal_say import API_VERSION, SERVICE_NAME, __version__
from opal_say.config import ServiceConfig
from opal_say.persistence.store import Store
from opal_say.service.catalog import EngineService, PersonaService, ProfileService, VoiceService
from opal_say.service.config_ops import ConfigService
from opal_say.service.errors import ApiError
from opal_say.service.generation import GenerationService
from opal_say.service.lifecycle import ServiceProcessManager
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
    voice: VoiceService
    profile: ProfileService
    persona: PersonaService
    setting: SettingsService
    generation: GenerationService
    config_ops: ConfigService
    lifecycle: ServiceProcessManager

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
        speech = self.generation.default_path()
        return {
            "name": SERVICE_NAME,
            "api_version": API_VERSION,
            "status": status,
            "version": __version__,
            "live": True,
            "ready": bool(speech["ok"]),
            "active_profile_id": str(active["id"]) if active else None,
            "details": {
                "engines": engine_states,
                "speech": speech,
                "voices": len(self.store.list_voices()),
                "enabled_voices": len(self.store.list_voices(enabled=True)),
                "profile": len(self.store.list_profiles()),
            },
        }

    def readiness(self) -> dict[str, Any]:
        """Readiness probe: ready iff the default speech path can synthesize.

        Engine states stay visible alongside the verdict (`engines`), but they no
        longer decide it: an enabled-and-available engine is necessary, not
        sufficient — readiness additionally requires the default voice resolution
        used by generation (active profile -> voice -> engine) to succeed, and the
        resolved voice's options and host assets to check out. Failures report the
        resolution stage (`details.speech.code`) so the missing/disabled/invalid
        default is actionable instead of a bare 503.
        """
        health = self.health()
        speech = self.generation.default_path()
        body = {
            "status": health["status"] if speech["ok"] else "unavailable",
            "ready": bool(speech["ok"]),
            "version": health["version"],
            "details": {**health["details"], "speech": speech},
        }
        return body

    def close(self) -> None:
        self.store.close()


def build_services(config: ServiceConfig, engines: list[TtsEngine] | None = None) -> Services:
    store = Store(config.db_path)
    fresh = store.created
    runtime = EngineRuntimeManager(EngineRegistry(engines if engines is not None else default_engines()))
    engine_svc = EngineService(store, runtime)
    engine_svc.bootstrap(config.engines)
    voice_svc = VoiceService(store, engine_svc)
    setting_svc = SettingsService(store)
    services = Services(
        config=config,
        store=store,
        runtime=runtime,
        engine=engine_svc,
        voice=voice_svc,
        profile=ProfileService(store, engine_svc, voice_svc),
        persona=PersonaService(store, voice_svc),
        setting=setting_svc,
        generation=GenerationService(
            store, engine_svc, setting_svc, owner_url=config.persona_owner_url,
            owner_token_env=config.persona_owner_token_env,
        ),
        config_ops=ConfigService(store, engine_svc, config.config_dir, config.backup_dir),
        lifecycle=ServiceProcessManager(config),
    )
    if fresh and config.config_dir.is_dir():
        try:
            result = services.config_ops.import_config()
            if not result["success"]:
                log.warning("initial config import skipped: %s", [i.message for i in result["validation"].errors])
        except ApiError as exc:
            log.info("no initial configuration imported: %s", exc.message)
    return services
