"""REST endpoints. Every operation's `operation_id` equals the contract's `operationId`."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from typing import Any

import anyio
from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, Path, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from opal_say.api.correlation import correlation_ids
from opal_say.schemas import (
    ConfigSaveRequest,
    ConfigSnapshotRequest,
    GenerateRequest,
    GenerateStreamRequest,
    HealthRead,
    IncrementalStreamParams,
    ProfileCreate,
    ProfileRead,
    ProfileUpdate,
    ServiceLogTail,
    ServiceStartOptions,
    ServiceStatus,
    SettingRead,
    SettingUpdate,
    ValidationResult,
    VoiceCreate,
    VoiceRead,
    VoiceTestRequest,
    VoiceUpdate,
)
from opal_say.service.concurrency import config_etag, mutate_with_revision
from opal_say.service.container import Services
from opal_say.service.errors import GenerationFailed, InvalidRequest, NotFound, PayloadTooLarge
from opal_say.tts.base import Report
from opal_say.tts.chunking import chunk_text, split_stream_ready

router = APIRouter()

log = logging.getLogger(__name__)


def _correlation_context(request: Request) -> str:
    """Flatten the request's correlation identifiers for log records."""
    ids = correlation_ids(request)
    if ids is None:
        return "request_id=unknown"
    parts = [f"request_id={ids.request_id}", f"transaction_id={ids.transaction_id}"]
    if ids.session_id:
        parts.append(f"session_id={ids.session_id}")
    if ids.causation_id:
        parts.append(f"causation_id={ids.causation_id}")
    return " ".join(parts)


# Poll slice for buffer put/get waits inside the PCM stream machinery.
_STREAM_POLL_S = 0.1


async def _watch_disconnect(request: Request, cancel: threading.Event) -> None:
    """Set `cancel` once the client's connection drops (http.disconnect)."""
    try:
        while True:
            message = await request.receive()
            if message["type"] == "http.disconnect":
                cancel.set()
                return
    except Exception:  # noqa: BLE001 - a receiver failure only ends the watch
        return


class _PcmBodyIterator:
    """Async iterator feeding the synchronous PCM generator of one stream request.

    A producer thread runs the generator into a bounded buffer (`buffer_blocks`),
    decoupling engine synthesis from client consumption speed. When the buffer
    stays full beyond `lag_timeout_ms` — the client fell behind — the producer
    abandons generation instead of holding the inference permit for a stalled
    reader; buffered blocks still drain before the stream ends. `close()` signals
    the producer to stop and releases the plan-bound runtime reference exactly
    once; the producer thread owns generator teardown so close() never races a
    suspended `next()`.
    """

    def __init__(self, blocks, ctx: str, on_close=None, buffer_blocks: int = 8,
                 lag_timeout_ms: int = 30_000, cancel: threading.Event | None = None):
        self._blocks = blocks
        self._ctx = ctx
        self._on_close = on_close
        self._lag_timeout = lag_timeout_ms / 1000
        self._cancel = cancel or threading.Event()
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=max(1, buffer_blocks))
        self._producer_done = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        self._lock = threading.Lock()
        self._first: bytes | None = None
        self._first_pending = False
        self._started = False
        self._completed = False
        self._failed = False
        self._closed = False

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if self._first_pending:
            self._first_pending = False
            if self._first is not None:
                return self._first
        try:
            block = await run_in_threadpool(self.next_block)
        except Exception:
            # A synthesis failure after 200: the contract terminates the stream
            # rather than injecting an error object into the byte stream.
            self._failed = True
            log.warning("audio stream terminated by synthesis failure (%s)", self._ctx, exc_info=True)
            raise
        if block is None:
            self._completed = True
            log.debug("audio stream completed (%s)", self._ctx)
            raise StopAsyncIteration
        return block

    def prime(self, first: bytes | None) -> None:
        """Seed the block already produced while deciding whether 200 is commitable."""
        self._first = first
        self._first_pending = True

    def _produce(self) -> None:
        """Run the generator into the bounded queue; owns the generator's lifecycle."""
        full_since: float | None = None
        try:
            for block in self._blocks:
                while True:
                    if self._closed or self._cancel.is_set():
                        return
                    try:
                        self._queue.put(block, timeout=_STREAM_POLL_S)
                        full_since = None
                        break
                    except queue.Full:
                        if full_since is None:
                            full_since = time.monotonic()
                        elif time.monotonic() - full_since >= self._lag_timeout:
                            log.warning(
                                "audio stream abandoned: buffer undrained for %.1fs (%s)",
                                self._lag_timeout, self._ctx,
                            )
                            return
        except Exception as exc:  # noqa: BLE001 - surfaced to the consumer via next_block
            self._error = exc
        finally:
            try:
                self._blocks.close()
            except Exception:  # noqa: BLE001 - teardown failure must not mask the outcome
                log.exception("failed to release audio stream resources (%s)", self._ctx)
            self._producer_done.set()

    def next_block(self) -> bytes | None:
        with self._lock:
            if self._closed:
                return None
            if not self._started:
                self._started = True
                self._thread = threading.Thread(
                    target=self._produce, name="pcm-stream-producer", daemon=True
                )
                self._thread.start()
        while True:
            if self._closed:
                return None
            try:
                return self._queue.get(timeout=_STREAM_POLL_S)
            except queue.Empty:
                if self._producer_done.is_set():
                    if self._error is not None:
                        raise self._error from None
                    return None

    def close(self) -> None:
        """Release the stream exactly once.

        Signals the producer to stop and joins it; the producer closes the
        generator itself (it's the iterating thread, so no `generator already
        executing` race), then `_on_close` releases the plan-bound runtime —
        also covering the never-started case. The join may wait out a running
        engine call, bounded by that call's own duration.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._cancel.set()
            thread = self._thread
        if thread is not None:
            thread.join()
        try:
            # The plan-bound runtime: released even when the producer never ran
            # a generator body (never started) or raised.
            if self._on_close is not None:
                self._on_close()
        except Exception:  # noqa: BLE001
            log.exception("failed to release the bound generation (%s)", self._ctx)
        if not self._completed and not self._failed:
            log.info("audio stream closed before completion (%s)", self._ctx)


class _PcmStreamResponse(StreamingResponse):
    """StreamingResponse that releases the PCM generator when the response ends."""

    def __init__(self, stream: _PcmBodyIterator, **kwargs):
        super().__init__(stream, **kwargs)
        self._stream = stream

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Every exit — completed, failed, disconnected or externally cancelled —
            # unwinds through here, so the runtime reference and inference permit are
            # released deterministically instead of relying on garbage collection.
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(self._stream.close)


class _DuplexPcmStreamResponse(_PcmStreamResponse):
    """Stream response for the incremental endpoint, whose request body stays open.

    `StreamingResponse.__call__` runs `listen_for_disconnect(receive)` alongside
    the body send — a second consumer that competes for and discards request-body
    messages, so it cannot be used while NDJSON input is still arriving. Instead
    send chunks directly; a dropped client surfaces as ClientDisconnect in the
    NDJSON reader or as a send failure, both of which unwind through the
    `finally` below.
    """

    async def __call__(self, scope, receive, send):
        try:
            await send({
                "type": "http.response.start",
                "status": self.status_code,
                "headers": self.raw_headers,
            })
            async for chunk in self.body_iterator:
                if not isinstance(chunk, bytes | memoryview):
                    chunk = chunk.encode(self.charset)
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            with anyio.CancelScope(shield=True):
                await run_in_threadpool(self._stream.close)

ProfileId = Path(min_length=1, description="Opaque profile identifier.")
BackupFilename = Path(min_length=1, max_length=255, pattern=r"^[^/\\]+$")
SettingKey = Path(min_length=1)


def services(request: Request) -> Services:
    return request.app.state.services


def report_dict(report: Report) -> dict[str, Any]:
    def msg(i):
        return {"code": i.code, "message": i.message, "field": i.field, "details": i.details}

    return {
        "valid": report.valid,
        "errors": [msg(i) for i in report.errors],
        "warnings": [msg(i) for i in report.warnings],
        "details": report.details,
    }


# -- voice ----------------------------------------------------------------------


@router.get("/api/v1/voice/", tags=["voice"], operation_id="list_voices",
            response_model=list[VoiceRead])
def list_voice(
    response: Response,
    engine_id: str | None = Query(None, min_length=1),
    enabled: bool | None = Query(None),
    language: str | None = Query(None, min_length=1),
    svc: Services = Depends(services),
):
    response.headers["ETag"] = config_etag(svc.store)
    return svc.voice.list(engine_id, enabled, language)


@router.post("/api/v1/voice/", tags=["voice"], operation_id="create_voice",
             response_model=VoiceRead, status_code=201)
def create_voice(body: VoiceCreate, response: Response, if_match: str | None = Header(None),
                 svc: Services = Depends(services)):
    voice = mutate_with_revision(svc.store, if_match, lambda: svc.voice.create(body))
    response.headers["Location"] = f"/api/v1/voice/{voice['voice_id']}"
    return voice


@router.get("/api/v1/voice/{voice_id}", tags=["voice"], operation_id="show_voice",
            response_model=VoiceRead)
def get_voice(voice_id: str, response: Response, svc: Services = Depends(services)):
    response.headers["ETag"] = config_etag(svc.store)
    return svc.voice.get(voice_id)


@router.patch("/api/v1/voice/{voice_id}", tags=["voice"], operation_id="update_voice",
              response_model=VoiceRead)
def update_voice(voice_id: str, body: VoiceUpdate, if_match: str | None = Header(None),
                svc: Services = Depends(services)):
    return mutate_with_revision(svc.store, if_match, lambda: svc.voice.update(voice_id, body))


@router.delete("/api/v1/voice/{voice_id}", tags=["voice"],
               operation_id="remove_voice", status_code=204)
def delete_voice(voice_id: str, if_match: str | None = Header(None), svc: Services = Depends(services)):
    mutate_with_revision(svc.store, if_match, lambda: svc.voice.delete(voice_id))
    return Response(status_code=204)


@router.post("/api/v1/voice/{voice_id}/validate", tags=["voice"],
             operation_id="validate_voice", response_model=ValidationResult)
def validate_voice(voice_id: str, svc: Services = Depends(services)):
    return report_dict(svc.voice.validate(voice_id))


# Deliberate asymmetry: CRUD handlers are sync `def` (FastAPI runs them in the threadpool
# automatically); the three audio handlers are `async` because they must control *when* work
# is dispatched — generate/voice-test produce the first block before 200 is committed, and
# stream keeps a generator alive for the response body.
@router.post("/api/v1/voice/{voice_id}/test", tags=["voice"],
             operation_id="test_voice", response_class=Response)
async def test_voice(request: Request, voice_id: str, body: VoiceTestRequest | None = Body(None),
                     svc: Services = Depends(services)):
    cancel = threading.Event()
    watch = asyncio.get_running_loop().create_task(_watch_disconnect(request, cancel))
    try:
        plan, data, media_type = await run_in_threadpool(
            svc.generation.test_voice, voice_id, body or VoiceTestRequest(), cancel.is_set
        )
        return Response(data, media_type=media_type, headers=plan.headers())
    finally:
        watch.cancel()




# -- profile --------------------------------------------------------------------


@router.get("/api/v1/profile/", tags=["profile"], operation_id="opal_profile_list",
            response_model=list[ProfileRead])
def list_profile(response: Response, svc: Services = Depends(services)):
    response.headers["ETag"] = config_etag(svc.store)
    return svc.profile.list()


@router.post("/api/v1/profile/", tags=["profile"], operation_id="opal_profile_create",
             response_model=ProfileRead, status_code=201)
def create_profile(body: ProfileCreate, response: Response, if_match: str | None = Header(None),
                   svc: Services = Depends(services)):
    profile = mutate_with_revision(svc.store, if_match, lambda: svc.profile.create(body))
    response.headers["Location"] = f"/api/v1/profile/{profile['id']}"
    return profile


@router.get("/api/v1/profile/{profile_id}", tags=["profile"],
            operation_id="opal_profile_get", response_model=ProfileRead)
def get_profile(response: Response, profile_id: str = ProfileId, svc: Services = Depends(services)):
    response.headers["ETag"] = config_etag(svc.store)
    return svc.profile.get(profile_id)


@router.patch("/api/v1/profile/{profile_id}", tags=["profile"],
              operation_id="opal_profile_patch", response_model=ProfileRead)
def update_profile(body: ProfileUpdate, profile_id: str = ProfileId, if_match: str | None = Header(None),
                   svc: Services = Depends(services)):
    return mutate_with_revision(svc.store, if_match, lambda: svc.profile.update(profile_id, body))


@router.delete("/api/v1/profile/{profile_id}", tags=["profile"],
               operation_id="opal_profile_delete", status_code=204)
def delete_profile(profile_id: str = ProfileId, if_match: str | None = Header(None), svc: Services = Depends(services)):
    mutate_with_revision(svc.store, if_match, lambda: svc.profile.delete(profile_id))
    return Response(status_code=204)


@router.post("/api/v1/profile/{profile_id}/activate", tags=["profile"],
             operation_id="opal_profile_activate", response_model=ProfileRead)
def activate_profile(profile_id: str = ProfileId, if_match: str | None = Header(None),
                     svc: Services = Depends(services)):
    return mutate_with_revision(svc.store, if_match, lambda: svc.profile.activate(profile_id))


# -- generate --------------------------------------------------------------------


@router.post("/api/v1/generate", tags=["generate"], operation_id="generate_speech",
             response_class=Response)
async def generate_audio(request: Request, body: GenerateRequest, svc: Services = Depends(services)):
    # Admission and per-chunk permit waits abort promptly if the client drops.
    cancel = threading.Event()
    watch = asyncio.get_running_loop().create_task(_watch_disconnect(request, cancel))
    try:
        plan = await run_in_threadpool(svc.generation.plan, body, cancel.is_set)
        data, media_type = await run_in_threadpool(svc.generation.render, plan)
        return Response(data, media_type=media_type, headers=plan.headers())
    finally:
        watch.cancel()


@router.post("/api/v1/generate/stream", tags=["generate"], operation_id="stream_speech",
             response_class=StreamingResponse)
async def stream_audio(request: Request, body: GenerateStreamRequest, svc: Services = Depends(services)):
    cancel = threading.Event()
    loop = asyncio.get_running_loop()
    watch = loop.create_task(_watch_disconnect(request, cancel))
    try:
        plan = await run_in_threadpool(svc.generation.plan, body, cancel.is_set)
    except BaseException:
        watch.cancel()
        raise
    stream = _PcmBodyIterator(
        svc.generation.pcm_stream(plan),
        _correlation_context(request),
        on_close=lambda: (loop.call_soon_threadsafe(watch.cancel), plan.release()),
        buffer_blocks=int(svc.setting.effective("stream_buffer_blocks")),
        lag_timeout_ms=int(svc.setting.effective("stream_lag_timeout_ms")),
        cancel=cancel,
    )
    try:
        # Produce the first block before committing to 200 so early failures become JSON errors.
        first = await run_in_threadpool(stream.next_block)
    except BaseException:
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(stream.close)
        raise
    stream.prime(first)
    return _PcmStreamResponse(stream, media_type="application/octet-stream", headers=plan.headers("pcm"))


# -- incremental generation ---------------------------------------------------------

_INCREMENT_MAX_LINE = 64 << 10


class _NdjsonReader:
    """Line splitter over `request.stream()`; the handler reads the params line
    first, then hands the same reader to the feed task for text segments."""

    def __init__(self, request: Request):
        self._it = request.stream().__aiter__()
        self._buf = ""

    async def readline(self) -> str | None:
        while "\n" not in self._buf:
            if len(self._buf) > _INCREMENT_MAX_LINE:
                raise PayloadTooLarge(
                    f"incremental input line exceeds the {_INCREMENT_MAX_LINE}-byte limit"
                )
            try:
                chunk = await self._it.__anext__()
            except StopAsyncIteration:
                line, self._buf = self._buf, ""
                return line if line.strip() else None
            self._buf += chunk.decode("utf-8", "replace")
        line, self._buf = self._buf.split("\n", 1)
        return line


class _FeedError:
    """Terminal record carrying a reader-side failure into the chunk stream."""

    def __init__(self, exc: Exception):
        self.exc = exc


def _qput(q: queue.Queue, item, cancel: threading.Event) -> bool:
    """Bounded put; returns False when the stream is being torn down."""
    while True:
        if cancel.is_set():
            return False
        try:
            q.put(item, timeout=_STREAM_POLL_S)
            return True
        except queue.Full:
            continue


async def _feed_increments(reader: _NdjsonReader, q: queue.Queue, cancel: threading.Event,
                           setting, ctx: str) -> None:
    """Read NDJSON text segments and feed sentence-sized chunks to synthesis.

    Boundaries: `{"done": true}` or request-body EOF finishes input (trailing
    partial text is flushed); `cancel` — set by disconnect or stream teardown —
    aborts; a full queue blocks the read, so backpressure reaches the client's
    TCP send buffer instead of growing memory. Malformed records and the
    max_text_chars cap terminate the output stream with a stored error.
    """
    pending = ""
    total = 0
    max_chars = int(setting.effective("max_text_chars"))
    chunk_max = int(setting.effective("chunk_max_chars"))
    error: Exception | None = None
    try:
        while True:
            if cancel.is_set():
                return
            line = await reader.readline()
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                raise InvalidRequest.single(["body"], "malformed NDJSON record") from None
            if isinstance(rec, dict) and rec.get("done") is True:
                break
            seg = rec.get("text") if isinstance(rec, dict) else None
            if seg is not None and (not isinstance(seg, str) or not seg):
                raise InvalidRequest.single(["body"], "record must carry a non-empty 'text' string")
            if seg is None:
                if isinstance(rec, dict) and rec.get("flush") is True:
                    for c in chunk_text(pending, chunk_max):
                        await run_in_threadpool(_qput, q, c, cancel)
                    pending = ""
                    continue
                raise InvalidRequest.single(["body"], "record must carry a non-empty 'text' string")
            total += len(seg)
            if total > max_chars:
                raise PayloadTooLarge(
                    f"incremental text exceeds the configured maximum of {max_chars} characters",
                    details={"max_text_chars": max_chars},
                )
            pending += seg
            if rec.get("flush") is True:
                ready, pending = chunk_text(pending, chunk_max), ""
            else:
                ready, pending = split_stream_ready(pending, chunk_max)
            for c in ready:
                await run_in_threadpool(_qput, q, c, cancel)
    except ClientDisconnect:
        # Interrupted input: abort the stream rather than flushing partial text.
        cancel.set()
        return
    except (InvalidRequest, PayloadTooLarge) as exc:
        error = exc
    except Exception:  # noqa: BLE001 - reader/transport failure truncates the stream
        log.exception("incremental input reader failed (%s)", ctx)
        error = GenerationFailed("incremental text input failed")
    for c in (chunk_text(pending, chunk_max) if error is None and not cancel.is_set() else []):
        await run_in_threadpool(_qput, q, c, cancel)
    # Terminal record: the stored error wins when present, else the EOF sentinel.
    _qput(q, _FeedError(error) if error is not None else None, cancel)


def _increment_chunks(q: queue.Queue, cancel: threading.Event):
    """Generator feeding pcm_stream from the incremental chunk queue."""
    while True:
        if cancel.is_set():
            return
        try:
            item = q.get(timeout=_STREAM_POLL_S)
        except queue.Empty:
            continue
        if item is None:
            return
        if isinstance(item, _FeedError):
            raise item.exc
        yield item


@router.post("/api/v1/generate/stream/incremental", tags=["generate"],
             operation_id="stream_speech_incremental", response_class=StreamingResponse)
async def stream_audio_incremental(request: Request, svc: Services = Depends(services)):
    """Streaming PCM for text supplied incrementally as NDJSON lines.

    Line 1 carries the generation parameters (`IncrementalStreamParams`); later
    lines carry `{"text": "..."}` segments or `{"done": true}`. First audio
    becomes available before the producer finishes supplying text.
    """
    # Unlike the one-shot stream endpoints there is no _watch_disconnect task:
    # it would compete with the NDJSON reader for the request body. A dropped
    # connection surfaces as ClientDisconnect in the feed reader or as a send
    # failure while the response streams — both unwind through _cleanup.
    cancel = threading.Event()
    loop = asyncio.get_running_loop()
    reader = _NdjsonReader(request)
    params_line = await reader.readline()
    if params_line is None:
        raise InvalidRequest.single(["body"], "first NDJSON line must carry the generation parameters")
    try:
        raw = json.loads(params_line)
    except ValueError:
        raise InvalidRequest.single(["body"], "first NDJSON line must be a JSON object") from None
    try:
        params = IncrementalStreamParams.model_validate(raw)
    except ValidationError as exc:
        raise InvalidRequest(exc.errors()) from None
    plan = await run_in_threadpool(svc.generation.plan_incremental, params, cancel.is_set)
    chunks_q: queue.Queue = queue.Queue(
        maxsize=int(svc.setting.effective("incremental_pending_chunks"))
    )
    feed = loop.create_task(_feed_increments(reader, chunks_q, cancel, svc.setting,
                                             _correlation_context(request)))

    def _cleanup():
        cancel.set()
        loop.call_soon_threadsafe(feed.cancel)
        plan.release()

    stream = _PcmBodyIterator(
        svc.generation.pcm_stream(plan, text_source=_increment_chunks(chunks_q, cancel)),
        _correlation_context(request),
        on_close=_cleanup,
        buffer_blocks=int(svc.setting.effective("stream_buffer_blocks")),
        lag_timeout_ms=int(svc.setting.effective("stream_lag_timeout_ms")),
        cancel=cancel,
    )
    try:
        first = await run_in_threadpool(stream.next_block)
    except BaseException:
        with anyio.CancelScope(shield=True):
            await run_in_threadpool(stream.close)
        raise
    stream.prime(first)
    return _DuplexPcmStreamResponse(stream, media_type="application/octet-stream", headers=plan.headers("pcm"))


# -- setting --------------------------------------------------------------------


@router.get("/api/v1/setting/", tags=["setting"], operation_id="opal_setting_list")
def get_all_setting(response: Response, svc: Services = Depends(services)) -> dict[str, Any]:
    response.headers["ETag"] = config_etag(svc.store)
    return svc.setting.all()


@router.get("/api/v1/setting/{key}", tags=["setting"], operation_id="opal_setting_get",
            response_model=SettingRead, response_model_exclude_none=True)
def get_setting(response: Response, key: str = SettingKey, svc: Services = Depends(services)):
    response.headers["ETag"] = config_etag(svc.store)
    return svc.setting.get(key)


@router.put("/api/v1/setting/{key}", tags=["setting"], operation_id="opal_setting_put",
            response_model=SettingRead, response_model_exclude_none=True)
def upsert_setting(body: SettingUpdate, key: str = SettingKey, if_match: str | None = Header(None),
                   svc: Services = Depends(services)):
    return mutate_with_revision(svc.store, if_match, lambda: svc.setting.upsert(key, body.value))


@router.delete("/api/v1/setting/{key}", tags=["setting"], operation_id="opal_setting_delete",
               status_code=204)
def delete_setting(key: str = SettingKey, if_match: str | None = Header(None), svc: Services = Depends(services)):
    mutate_with_revision(svc.store, if_match, lambda: svc.setting.delete(key))
    return Response(status_code=204)


# -- health ----------------------------------------------------------------------


@router.get("/api/v1/health/", tags=["health"], operation_id="opal_health_get",
            response_model=HealthRead)
def health_check(svc: Services = Depends(services)):
    return svc.health()


@router.get("/api/v1/health/live", tags=["health"], operation_id="opal_health_live_get")
def health_live() -> dict[str, Any]:
    return {"status": "ok", "ready": True}


@router.get("/api/v1/health/ready", tags=["health"], operation_id="opal_health_ready_get")
def health_ready(svc: Services = Depends(services)):
    probe = svc.readiness()
    return JSONResponse(probe, status_code=200 if probe["ready"] else 503)


# -- backup ----------------------------------------------------------------------


async def config_body(request: Request):
    if (await request.body()).strip() == b"null":
        raise InvalidRequest.single(["body"], "configuration request must be an object", "dict_type")


@router.post("/api/v1/config/save", tags=["config"], operation_id="opal_config_save", dependencies=[Depends(config_body)])
def save_config(response: Response, body: ConfigSaveRequest | None = None, svc: Services = Depends(services)):
    result, revision = svc.config_ops.save_snapshot(body.destination if body else "managed")
    response.headers["ETag"] = revision
    return result


@router.post("/api/v1/config/load", tags=["config"], operation_id="opal_config_load", dependencies=[Depends(config_body)])
def load_config(response: Response, body: ConfigSnapshotRequest | None = None,
                if_match: str | None = Header(None), svc: Services = Depends(services)):
    def apply():
        result = svc.config_ops.load_snapshot(body.snapshot if body else None)
        response.headers["ETag"] = config_etag(svc.store)
        return result

    return mutate_with_revision(svc.store, if_match, apply)


@router.post("/api/v1/config/validate", tags=["config"], operation_id="opal_config_validate",
             dependencies=[Depends(config_body)])
def validate_config(body: ConfigSnapshotRequest | None = None, svc: Services = Depends(services)):
    """Validate a supplied configuration payload against host resources. No writes."""
    with svc.store.write_lock():
        return svc.config_ops.validate_snapshot(body.snapshot if body else None)


# Configuration snapshots replace the retired backup routes.


# -- service process lifecycle -----------------------------------------------------

# "The service's server process" is the pidfile-tracked detached child managed by
# ServiceProcessManager. When the tracked pid is this process itself (the service was
# launched via `service start`), the response is still 200 — termination is deferred
# to a BackgroundTask so it lands after the response is sent.


@router.post("/api/v1/service/start", tags=["service"], operation_id="opal_service_start",
             response_model=ServiceStatus)
def start_service(background_tasks: BackgroundTasks, body: ServiceStartOptions | None = Body(None),
                  svc: Services = Depends(services)):
    status, terminate_self = svc.lifecycle.start(append=body.append if body else False)
    if terminate_self:
        background_tasks.add_task(svc.lifecycle.terminate_self)
    return status


@router.post("/api/v1/service/stop", tags=["service"], operation_id="opal_service_stop",
             response_model=ServiceStatus)
def stop_service(background_tasks: BackgroundTasks, svc: Services = Depends(services)):
    status, terminate_self = svc.lifecycle.stop()
    if terminate_self:
        background_tasks.add_task(svc.lifecycle.terminate_self)
    return status


@router.get("/api/v1/service/log", tags=["service"], operation_id="opal_service_log",
            response_model=ServiceLogTail)
def tail_service_log(lines: int = Query(10, ge=1, le=1000), svc: Services = Depends(services)):
    tail = svc.lifecycle.tail_log(lines)
    if tail is None:
        raise NotFound(f"no service log at {svc.lifecycle.log_file}")
    return tail


# -- service identity ---------------------------------------------------------------
