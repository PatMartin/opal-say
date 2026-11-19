"""Engine-neutral generation: setting resolution, chunking, resampling, gain and encoding."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

import httpx

from opal_say.persistence.store import Store, now_iso
from opal_say.schemas import (
    GenerateRequest,
    GenerateStreamRequest,
    IncrementalStreamParams,
    VoiceTestRequest,
)
from opal_say.service.catalog import EngineService
from opal_say.service.errors import (
    ApiError,
    ClientDisconnected,
    Conflict,
    GenerationFailed,
    InvalidRequest,
    NotFound,
    PayloadTooLarge,
    Unavailable,
)
from opal_say.service.settings import SettingsService
from opal_say.tts.audio import WAV_HEADER_BYTES, resample, to_pcm16, wav_bytes
from opal_say.tts.base import (
    AdmissionCancelledError,
    EngineOptionsError,
    EngineUnavailableError,
    TtsEngine,
    VoiceResolutionError,
)
from opal_say.tts.chunking import chunk_text
from opal_say.tts.runtime import EngineRuntime

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class GenerationPlan:
    """A resolved generation request bound to one runtime generation.

    `runtime` is acquired inside plan() and pins the engine generation the request
    was validated against: reconfiguration defers until it drains, so routing,
    option validation, sample-rate resolution and synthesis all observe one
    consistent engine configuration. Ownership transfers to the consumer:
    render()/pcm_stream()/test_voice() release it on every exit path, and
    release() itself is idempotent for early-close or abandoned-plan paths.
    """

    chunks: list[str]
    voice: dict[str, Any]
    engine: TtsEngine
    profile_id: str | None
    speed: float
    volume: float
    output_format: str
    sample_rate: int
    options: dict[str, Any]
    runtime: EngineRuntime

    def release(self) -> None:
        """Detach the bound runtime reference (idempotent)."""
        self.runtime.detach()

    def headers(self, output_format: str | None = None) -> dict[str, str]:
        headers = {
            "X-Opal-Audio-Format": output_format or self.output_format,
            "X-Opal-Sample-Rate": str(self.sample_rate),
            "X-Opal-Channels": "1",
            "X-Opal-Sample-Format": "s16le",
            "X-Opal-Engine-Id": self.engine.engine_id,
            "X-Opal-Voice-Id": self.voice["voice_id"],
        }
        if self.profile_id is not None:
            headers["X-Opal-Profile-Id"] = str(self.profile_id)
        return headers


def _first(*values: Any) -> Any:
    return next((v for v in values if v is not None), None)


def translate_engine_error(exc: Exception) -> Exception:
    """Map adapter exceptions onto API errors."""
    if isinstance(exc, ApiError | InvalidRequest):
        return exc
    if isinstance(exc, AdmissionCancelledError):
        return ClientDisconnected(str(exc))
    if isinstance(exc, EngineOptionsError):
        return InvalidRequest.from_issues(["body", "engine_options"], exc.issues, {})
    if isinstance(exc, VoiceResolutionError):
        return Conflict(str(exc), exc.code)
    if isinstance(exc, EngineUnavailableError):
        return Unavailable(str(exc))
    return GenerationFailed(str(exc) or exc.__class__.__name__)


class GenerationService:
    def __init__(
        self, store: Store, engine: EngineService, setting: SettingsService, owner_url: str | None = None,
        owner_token_env: str = "OPAL_SAY_PERSONA_OWNER_TOKEN",
    ):
        self.store = store
        self.engine = engine
        self.setting = setting
        self.owner_url = owner_url
        self.owner_token_env = owner_token_env
        # Per-engine serialization is owned by EngineRuntimeManager (engine.runtime).

    def resolve_persona(self, persona_id: str) -> dict[str, Any] | None:
        local = self.store.get_persona(persona_id)
        if not self.owner_url:
            return local
        cached = self.store.get_owner_persona(persona_id)
        response = self._owner_get(persona_id, cached.get("owner_etag") if cached else None)
        if response is None or response.status_code in {304, 401, 403} or response.status_code >= 500:
            return cached or local
        if response.status_code == 404:
            if cached:
                cached = {**cached, "enabled": False}
                self.store.cache_owner_persona(persona_id, cached)
                return cached
            return local
        if response.status_code != 200:
            return cached or local
        from opal_say.schemas import PersonaRead

        try:
            raw = response.json()
            document = PersonaRead.model_validate({"created_at": now_iso(), "updated_at": now_iso(), **raw}).model_dump()
            if document["persona_id"] != persona_id:
                return cached or local
        except ValueError:
            return cached or local
        document.update(origin="owner", owner_etag=response.headers.get("ETag"))
        self.store.cache_owner_persona(persona_id, document)
        return document

    def _owner_get(self, persona_id: str, etag: str | None = None) -> httpx.Response | None:
        """Authenticated GET against the persona owner (OPAL Think).

        The bearer token comes from `owner_token_env` — resolved per call so the
        token can rotate without a restart. `etag` carries the last seen owner
        ETag for version-aware revalidation (`If-None-Match`)."""
        from opal_say.api.correlation import CURRENT_IDS

        ids = CURRENT_IDS.get()
        headers: dict[str, str] = ids.response_headers() if ids else {}
        if ids:
            headers["X-Opal-Causation-Id"] = ids.request_id
            headers["X-Opal-Request-Id"] = uuid.uuid4().hex
        token = os.environ.get(self.owner_token_env) if self.owner_token_env else None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if etag:
            headers["If-None-Match"] = etag
        try:
            return httpx.get(
                f"{self.owner_url}/api/v1/persona/{quote(persona_id, safe='')}", headers=headers, timeout=5.0
            )
        except httpx.HTTPError:
            return None

    @staticmethod
    def _owner_row(data: dict[str, Any], etag: str | None, now: str) -> dict[str, Any]:
        return {
            "persona_id": data["persona_id"],
            "name": data["name"],
            "description": data.get("description"),
            "voice_id": data.get("voice_id"),
            "enabled": int(data.get("enabled", True)),
            "origin": "owner",
            "owner_etag": etag,
            "owner_checked_at": now,
            "created_at": data.get("created_at") or now,
            "updated_at": data.get("updated_at") or now,
        }

    def _fetch_owner_persona(self, persona_id: str) -> dict[str, Any] | None:
        """Fetch an unknown persona from the authoritative owner (OPAL Think) and
        store it as a synced ``origin: owner`` record. Any fetch failure falls back
        to the normal not-registered error."""
        resp = self._owner_get(persona_id)
        if resp is None or resp.status_code != 200:
            return None
        self.store.insert_persona(
            self._owner_row(resp.json(), resp.headers.get("ETag"), now_iso())
        )
        return self.store.get_persona(persona_id)

    def _refresh_owner_persona(self, row: dict[str, Any]) -> dict[str, Any]:
        """Revalidate a synced persona against its owner and apply the outcome.

        Version-aware: the last seen owner ETag goes out as If-None-Match and
        the stored row is only rewritten when the owner revision or record
        actually changed. Owner-side changes propagate as follows:

        * voice/description edits → applied to the cached row;
        * disablement or deletion → the persona is disabled locally;
        * unreachable owner, auth failure, 304 or unexpected status → keep the
          cached row so generation keeps working through a transient outage.

        `owner_checked_at` is bumped on every contact attempt so a slow or dead
        owner costs at most one HTTP call per `persona_refresh_seconds`.
        """
        persona_id = row["persona_id"]
        resp = self._owner_get(persona_id, etag=row.get("owner_etag"))
        now = now_iso()
        if resp is None:
            self.store.update_persona(persona_id, {"owner_checked_at": now})
            return self.store.get_persona(persona_id)
        if resp.status_code == 404:
            log.info("persona '%s' deleted at owner; disabling cached copy", persona_id)
            self.store.update_persona(persona_id, {"enabled": 0, "owner_checked_at": now})
            return self.store.get_persona(persona_id)
        if resp.status_code in (401, 403):
            log.warning("persona owner rejected %s credentials (status %s)", self.owner_token_env,
                        resp.status_code)
            self.store.update_persona(persona_id, {"owner_checked_at": now})
            return self.store.get_persona(persona_id)
        if resp.status_code == 304:
            self.store.update_persona(persona_id, {"owner_checked_at": now})
            return self.store.get_persona(persona_id)
        if resp.status_code != 200:
            self.store.update_persona(persona_id, {"owner_checked_at": now})
            return self.store.get_persona(persona_id)
        try:
            data = resp.json()
        except ValueError:
            self.store.update_persona(persona_id, {"owner_checked_at": now})
            return self.store.get_persona(persona_id)
        etag = resp.headers.get("ETag")
        if data.get("updated_at") == row.get("updated_at") and etag == row.get("owner_etag"):
            self.store.update_persona(persona_id, {"owner_checked_at": now})
        else:
            fresh = self._owner_row(data, etag, now)
            self.store.update_persona(persona_id, {
                "name": fresh["name"], "description": fresh["description"],
                "voice_id": fresh["voice_id"], "enabled": fresh["enabled"],
                "owner_etag": fresh["owner_etag"], "owner_checked_at": now,
                "updated_at": fresh["updated_at"],
            })
        return self.store.get_persona(persona_id)

    def _resolve_owner_persona(self, persona: dict[str, Any]) -> dict[str, Any]:
        """Refresh a synced persona when the cached copy is older than
        `persona_refresh_seconds`; otherwise return it unchanged. Local
        personas always take precedence and are never revalidated."""
        if persona.get("origin") != "owner":
            return persona
        interval = int(self.setting.effective("persona_refresh_seconds"))
        if interval <= 0:
            return self._refresh_owner_persona(persona)
        checked = persona.get("owner_checked_at")
        try:
            age = (
                (datetime.now(UTC) - datetime.fromisoformat(checked.replace("Z", "+00:00"))).total_seconds()
                if checked else float("inf")
            )
        except ValueError:
            age = float("inf")
        if age < interval:
            return persona
        return self._refresh_owner_persona(persona)


    # -- resolution -----------------------------------------------------------

    def _check_text(self, text: str) -> list[str]:
        limit = self.setting.effective("max_text_chars")
        if len(text) > limit:
            raise PayloadTooLarge(
                f"text length {len(text)} exceeds the configured maximum of {limit} characters",
                details={"max_text_chars": limit, "text_length": len(text)},
            )
        chunks = chunk_text(text, self.setting.effective("chunk_max_chars"))
        if not chunks:
            raise InvalidRequest.single(["body", "text"], "text contains nothing to speak", input_=text)
        return chunks

    def _route(
        self, voice: dict[str, Any], engine_override: str | None, cancelled=None
    ) -> tuple[TtsEngine, EngineRuntime]:
        """Resolve the target engine and bind its current runtime generation.

        The bound reference pins the configuration generation for the whole request:
        a reconfiguration defers until it drains, so every engine call this request
        makes — validation here, sample-rate resolution and synthesis — observes the
        same engine configuration. Any failure releases the binding before raising.
        """
        voice_id = voice["voice_id"]
        if not voice["enabled"]:
            raise Conflict(f"voice '{voice_id}' is disabled", "voice_disabled", {"voice_id": voice_id})
        engine_id = engine_override or voice["engine_id"]
        engine = self.engine.runtime.get(engine_id)
        if engine is None:
            raise Conflict(f"engine '{engine_id}' is not installed", "engine_not_found", {"engine_id": engine_id})
        try:
            runtime = self.engine.runtime.acquire(
                engine_id,
                timeout_ms=int(self.setting.effective("engine_acquire_timeout_ms")),
                cancelled=cancelled,
            )
        except (EngineUnavailableError, AdmissionCancelledError) as exc:
            raise translate_engine_error(exc) from exc
        try:
            if engine_id != voice["engine_id"]:
                mismatch = Conflict(
                    "The requested engine cannot use the resolved voice.",
                    "voice_engine_mismatch",
                    {"voice_id": voice_id, "requested_engine_id": engine_id},
                )
                if engine.validate_options(voice["engine_options"], "voice"):
                    raise mismatch
                try:
                    engine.describe_voice(voice["engine_voice_ref"], voice["engine_options"])
                except (VoiceResolutionError, EngineUnavailableError) as exc:
                    raise mismatch from exc
            if not self.engine.state(engine_id)["enabled"]:
                raise Conflict(f"engine '{engine_id}' is disabled", "engine_disabled", {"engine_id": engine_id})
            available, message = engine.availability()
            if not available:
                raise Unavailable(message or f"engine '{engine_id}' is unavailable", details={"engine_id": engine_id})
            return engine, runtime
        except Exception:
            self.engine.runtime.release(runtime)
            raise

    def _plan(
        self,
        *,
        chunks: list[str],
        voice: dict[str, Any],
        engine: TtsEngine,
        profile: dict[str, Any] | None,
        speed: float,
        volume: float,
        output_format: str,
        sample_rate: int | None,
        option_layers: list[dict[str, Any]],
        runtime: EngineRuntime,
    ) -> GenerationPlan:
        lo, hi = engine.speed_range
        if not lo <= speed <= hi:
            raise Conflict(
                f"speed {speed} is outside the range {lo}..{hi} supported by engine '{engine.engine_id}'",
                "unsupported_speed",
                {"engine_id": engine.engine_id, "speed": speed, "supported": [lo, hi]},
            )
        options: dict[str, Any] = {}
        for layer in option_layers:
            options.update(layer)
        issues = engine.validate_options(options, "voice")
        if issues:
            raise InvalidRequest.from_issues(["body", "engine_options"], issues, options)
        if sample_rate is None:
            try:
                sample_rate = engine.native_sample_rate(voice["engine_voice_ref"], voice["engine_options"])
            except (VoiceResolutionError, EngineUnavailableError) as exc:
                raise translate_engine_error(exc) from exc
        return GenerationPlan(
            chunks=chunks,
            voice=voice,
            engine=engine,
            profile_id=profile["id"] if profile else None,
            speed=float(speed),
            volume=float(volume),
            output_format=output_format,
            sample_rate=int(sample_rate),
            options=options,
            runtime=runtime,
        )

    def plan(
        self, req: GenerateRequest | GenerateStreamRequest, cancelled=None
    ) -> GenerationPlan:
        chunks = self._check_text(req.text)
        return self._resolve_plan(req, chunks, cancelled=cancelled)

    def plan_incremental(self, params: IncrementalStreamParams, cancelled=None) -> GenerationPlan:
        """Plan a stream whose text arrives incrementally after planning.

        Resolution is identical to plan(); the chunk list starts empty and the
        caller feeds chunks live via `pcm_stream(plan, text_source=...)`.
        """
        return self._resolve_plan(params, [], cancelled=cancelled)

    def _resolve_plan(
        self,
        req: GenerateRequest | GenerateStreamRequest | IncrementalStreamParams,
        chunks: list[str],
        cancelled=None,
    ) -> GenerationPlan:
        if req.profile_id is not None:
            profile = self.store.get_profile(req.profile_id)
            if profile is None:
                raise Conflict(f"profile {req.profile_id} not found", "profile_not_found", {"profile_id": req.profile_id})
        else:
            profile = self.store.active_profile()
        p = profile.get("modules", {}).get("opal-say", {}) if profile else {}

        persona_voice: str | None = None
        if req.persona is not None:
            persona = self.resolve_persona(req.persona)
            if persona is None:
                raise InvalidRequest.single(["body", "persona"], f"persona '{req.persona}' is not registered")
            if not persona["enabled"]:
                raise InvalidRequest.single(["body", "persona"], f"persona '{req.persona}' is disabled")
            persona_voice = persona["voice_id"]

        voice_id = _first(req.voice_id, persona_voice, p.get("voice_id"))
        if voice_id is None:
            raise Conflict(
                "no voice could be resolved: supply voice_id or a profile (or activate a profile)", "no_voice"
            )
        voice = self.store.get_voice(voice_id)
        if voice is None:
            if persona_voice == voice_id:
                raise InvalidRequest.single(["body", "persona_id"], "persona voice binding cannot be resolved")
            raise Conflict(f"voice '{voice_id}' is not registered", "voice_not_found", {"voice_id": voice_id})
        if persona_voice == voice_id and not voice["enabled"]:
            raise InvalidRequest.single(["body", "persona_id"], "persona voice binding is disabled")
        engine, runtime = self._route(voice, req.engine_id, cancelled)
        try:
            request_issues = engine.validate_options(req.engine_options, "generation")
            if request_issues:
                raise InvalidRequest.from_issues(["body", "engine_options"], request_issues, req.engine_options)

            output_format = _first(
                getattr(req, "output_format", None),
                p.get("output_format"),
                self.setting.effective("default_output_format"),
            )
            # Profile engine_options are engine-specific; they only apply when routing stays on the profile voice's engine.
            profile_options: dict[str, Any] = {}
            if profile:
                profile_voice = self.store.get_voice(p["voice_id"]) if p.get("voice_id") else voice
                if profile_voice and profile_voice["engine_id"] == engine.engine_id:
                    profile_options = p.get("engine_options", {})
            return self._plan(
                chunks=chunks,
                voice=voice,
                engine=engine,
                profile=profile,
                speed=_first(req.speed, p.get("speed"), self.setting.effective("default_speed")),
                volume=_first(req.volume, p.get("volume"), self.setting.effective("default_volume")),
                output_format=output_format,
                sample_rate=_first(req.sample_rate, p.get("sample_rate")),
                option_layers=[voice["engine_options"], profile_options, req.engine_options],
                runtime=runtime,
            )
        except Exception:
            self.engine.runtime.release(runtime)
            raise

    def plan_voice_test(
        self, voice: dict[str, Any], req: VoiceTestRequest, cancelled=None
    ) -> GenerationPlan:
        chunks = self._check_text(req.text)
        engine, runtime = self._route(voice, None, cancelled)
        try:
            issues = engine.validate_options(req.engine_options, "generation")
            if issues:
                raise InvalidRequest.from_issues(["body", "engine_options"], issues, req.engine_options)
            return self._plan(
                chunks=chunks,
                voice=voice,
                engine=engine,
                profile=None,
                speed=req.speed,
                volume=req.volume,
                output_format=req.output_format,
                sample_rate=req.sample_rate,
                option_layers=[voice["engine_options"], req.engine_options],
                runtime=runtime,
            )
        except Exception:
            self.engine.runtime.release(runtime)
            raise

    # -- readiness --------------------------------------------------------------

    def default_path(self) -> dict[str, Any]:
        """Probe the default speech path: what a generation request carrying no
        explicit voice, persona or profile would resolve to.

        Mirrors plan()'s resolution order (active profile -> voice -> engine) and
        checks the resolved voice's options and host assets, but never binds a
        runtime reference, so probing is cheap and can never queue behind
        inference permits or a deferred reconfiguration. Returns
        ``{"ok": True, ...resolved ids...}`` or ``{"ok": False, "code", "message", ...}``.
        """
        profile = self.store.active_profile()
        voice_id = profile["modules"].get("opal-say", {}).get("voice_id") if profile else None
        base: dict[str, Any] = {"profile_id": profile["id"] if profile else None, "voice_id": voice_id}
        if voice_id is None:
            return {**base, "ok": False, "code": "no_voice",
                    "message": "no default voice: no active profile supplies a voice_id"}
        voice = self.store.get_voice(voice_id)
        if voice is None:
            return {**base, "ok": False, "code": "voice_not_found",
                    "message": f"default voice '{voice_id}' is not registered"}
        if not voice["enabled"]:
            return {**base, "ok": False, "code": "voice_disabled",
                    "message": f"default voice '{voice_id}' is disabled"}
        base["engine_id"] = voice["engine_id"]
        problem = self.engine.usability_problem(voice["engine_id"])
        if problem is not None:
            return {**base, "ok": False, "code": problem[0], "message": problem[1]}
        engine = self.engine.runtime.get(voice["engine_id"])
        issues = engine.validate_options(voice["engine_options"], "voice")
        if issues:
            return {**base, "ok": False, "code": "invalid_options",
                    "message": "; ".join(i.message for i in issues)}
        try:
            desc = engine.describe_voice(voice["engine_voice_ref"], voice["engine_options"])
        except (VoiceResolutionError, EngineUnavailableError) as exc:
            return {**base, "ok": False, "code": getattr(exc, "code", "engine_unavailable"),
                    "message": str(exc)}
        return {**base, "ok": True, "native_sample_rate": desc.native_sample_rate}

    # -- synthesis ------------------------------------------------------------

    def pcm_stream(self, plan: GenerationPlan, text_source=None) -> Iterator[bytes]:
        """Yield S16LE mono PCM at `plan.sample_rate`, one block per engine audio chunk.

        `text_source` overrides `plan.chunks`: pass a live iterator for
        incremental input. Engine exceptions are translated to API errors so
        callers can report failures that occur before the first block as
        normal JSON errors.
        """
        ref = plan.voice["engine_voice_ref"]
        # The request is already bound to one runtime generation by plan(): synthesis
        # runs against the same configuration snapshot that validation resolved.
        runtime = plan.runtime
        try:
            for text in (text_source if text_source is not None else plan.chunks):
                try:
                    # Hold the inference permit for the whole iterator, but *consume*
                    # chunks incrementally instead of materializing the list.
                    with runtime:
                        for piece in runtime.engine.synthesize(ref, text, plan.speed, plan.options):
                            samples = resample(piece.samples, piece.sample_rate, plan.sample_rate)
                            if samples.size:
                                yield to_pcm16(samples, plan.volume)
                except Exception as exc:  # noqa: BLE001
                    raise translate_engine_error(exc) from exc
        finally:
            plan.release()

    def render(self, plan: GenerationPlan) -> tuple[bytes, str]:
        """Return the completed audio body and its media type.

        `max_audio_bytes` bounds the *encoded response body* — the bytes actually
        sent to the client. For PCM output that is the raw S16LE payload; for WAV
        it is the payload plus the fixed 44-byte container header, so the PCM
        budget is reduced by `WAV_HEADER_BYTES`. Non-streaming responses must
        buffer the whole output; accumulation stays bounded by the limit and an
        oversized result aborts early (the abandoned stream releases its runtime
        reference and inference permit) with 413 instead of growing memory
        unboundedly. `pcm_stream`, used by /generate/stream, emits each block
        incrementally and is governed by text and runtime resource limits —
        `max_text_chars`, inference permits — not this output-size limit.
        """
        limit = int(self.setting.effective("max_audio_bytes"))
        pcm_budget = limit - (WAV_HEADER_BYTES if plan.output_format == "wav" else 0)
        total, blocks = 0, []
        try:
            for block in self.pcm_stream(plan):
                total += len(block)
                if total > pcm_budget:
                    raise PayloadTooLarge(
                        f"generated audio exceeds the {limit}-byte limit",
                        details={"limit_bytes": limit, "produced_bytes": total},
                    )
                blocks.append(block)
        finally:
            # Covers raises before first iteration and aborts that leave the stream
            # suspended: the bound runtime is released without waiting for GC.
            plan.release()
        pcm = b"".join(blocks)
        if plan.output_format == "wav":
            return wav_bytes(pcm, plan.sample_rate), "audio/wav"
        return pcm, "application/octet-stream"

    def test_voice(
        self, voice_id: str, req: VoiceTestRequest, cancelled=None
    ) -> tuple[GenerationPlan, bytes, str]:
        """Lookup + plan + render for POST /voice/{id}/test, in one call."""
        voice = self.store.get_voice(voice_id)
        if voice is None:
            raise NotFound(f"voice '{voice_id}' not found", "voice_not_found", {"voice_id": voice_id})
        plan = self.plan_voice_test(voice, req, cancelled)
        data, media_type = self.render(plan)
        return plan, data, media_type
