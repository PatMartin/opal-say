# OPAL Say user guide

OPAL Say is a standalone local text-to-speech service with a REST API, a Typer CLI and a React console. Contract **0.5.0** keeps the URL prefix **`/api/v1`**. The packaged, self-contained contract served at `/openapi.json` is the authoritative operation/schema reference; `/docs` provides interactive exploration.

The server generates audio; playback happens on the client. Piper and Kokoro run locally when their optional dependencies and model files are installed. OPAL Think is optional, and standalone generation does not require an event service or event publication.

## Installation and first use

Python 3.11+ is required. Node 18+ is needed only to build the console.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[engines,dev]'
opal-say-svc --host 127.0.0.1 --port 8001
```

Use `.[piper]`, `.[kokoro]` or the core package when only selected adapters are needed. Model binaries are separate from Python packages and must be installed on the **service host**. See the license notes in the [README](../README.md).

From another terminal:

```bash
opal say service health
opal say voice list
opal say voice validate en-us-amy-low
opal say generate "Hello." --voice en-us-amy-low -o hello.wav
```

If the fresh seed catalog could not be imported because models are unavailable, configure an installed engine and register a voice explicitly. Engine options such as `voices_dir` are set in the `resources.engines` section of a configuration snapshot (see below) or the seed `config/*.yaml`, not a dedicated engine command:

```bash
opal say voice add amy --name Amy --engine piper --ref en_US-amy-low
opal say voice validate amy
opal say generate "Ready." --voice amy -o ready.wav
```

Configuration YAML is a first-database bootstrap input, not a live import interface. Existing databases retain their persisted configuration. Use snapshots to replace live configuration after startup.

## Deployment and authorization

Default listener: `127.0.0.1:8001`. Service configuration is read from `config/service.yaml`, the selected `--config`/`OPAL_SAY_CONFIG` path, and supported environment overrides. Relative data/config paths are resolved according to that configuration. Default persistent data lives under `var/`.

```yaml
service:
  host: 127.0.0.1
  port: 8001
  auth_enabled: true
  token_env: OPAL_SAY_READ_CREDENTIAL
  admin_token_env: OPAL_SAY_ADMIN_CREDENTIAL
```

The two named environment variables hold the read and administrator credentials. Do not put their values into YAML, database records or snapshots. Both roles may read; only the administrator may mutate configuration or control the service. Unauthenticated requests return 401; read credentials attempting writes return 403. Explicit `auth_enabled: false` is supported only on loopback. Non-loopback listeners require enabled authentication and configured credentials. Use an appropriately protected network/TLS termination when exposing the listener.

The CLI reads its bearer credential from `OPAL_SAY_TOKEN`. The console's credential field keeps it in memory only. Clearing/reloading the page discards it. Correlation IDs are not credentials.

## Concepts and resolution

| Resource | Meaning |
| --- | --- |
| Engine | Installed synthesis adapter plus desired enablement/options; runtime availability is separate |
| Voice | Registration pointing to existing host resources; removal never deletes model files |
| Profile | Portable document with opaque string `id`, `shared`, and `modules` compartments |
| Persona | Opaque string identity, optionally bound to a voice; may be local or owner-derived |
| Setting | Registered typed service default/limit, with a persisted override and effective provenance |
| Snapshot | Versioned desired configuration, not a media/model/archive backup |

Generation resolves explicit request values first. An explicit voice overrides a selected persona's voice. Profile defaults come from `modules.opal-say`, using the selected profile or otherwise the active one. Omitted values inherit service defaults. Engine options from a matching profile and request are shallow-merged, with request keys winning; defaults for a different engine are not accidentally applied.

Portable controls are speed (0.25–4.0, potentially narrowed by an adapter), linear volume (0–2), output format (`wav` or `pcm`) and output sample rate (8,000–192,000 Hz). Unsupported values are rejected, not silently clamped. Voice-native rate is used when no explicit/default rate is selected.

## REST conventions

All paths below are beneath `/api/v1`. Resource collection paths have trailing slashes; item paths do not. JSON request bodies use `Content-Type: application/json`.

### Errors, correlation and write preconditions

Errors use `{"error": {"code": "...", "message": "...", "request_id": "...", "transaction_id": "...", "details": {}}}`. Validation details include field locations where applicable. Session and causation IDs appear only when supplied.

The four context headers are `X-Opal-Session-Id`, `X-Opal-Transaction-Id`, `X-Opal-Request-Id`, and `X-Opal-Causation-Id`. Values must be printable ASCII tokens, 1–128 characters. Transaction/request IDs are generated when absent; a session is never forced. Downstream owner calls retain context and use a fresh request ID with the inbound request as causation.

Configuration reads return a strong whole-configuration ETag such as `"r12"`. Configuration mutations require that exact tag in `If-Match`: missing is 428, stale is 412, malformed is 422. A rejected write leaves stored values and revision unchanged. Successful mutations return the resulting revision. Browser clients can read ETag and OPAL response headers through CORS.

For a loopback deployment with authentication disabled:

```bash
BASE=http://127.0.0.1:8001
ETAG=$(curl -fsS -D - "$BASE/api/v1/setting/" -o /dev/null | tr -d '\r' | sed -n 's/^[Ee][Tt][Aa][Gg]: *//p')
curl -fS -X PUT "$BASE/api/v1/setting/default_speed" -H "If-Match: $ETAG" -H 'Content-Type: application/json' -d '{"value":1.1}'
```

When authentication is enabled, supply a bearer credential to **both** the read and write. Prefer the CLI's environment credential support rather than embedding credentials in shell history.

### Operations

| Workflow | Methods and paths | CLI |
| --- | --- | --- |
| Voices (7) | GET/POST `/voice/`; GET/PATCH/DELETE `/voice/{voice_id}`; POST `/voice/{voice_id}/validate`; POST `/voice/{voice_id}/test` | `voice list/add/get/update/remove/validate/test` |
| Profiles (6) | GET/POST `/profile/`; GET/PATCH/DELETE `/profile/{profile_id}`; POST `/profile/{profile_id}/activate` | `profile list/add/show/update/remove/activate` |
| Speech (3) | POST `/generate`, `/generate/stream`, `/generate/stream/incremental` | `generate`, `--stream`, `--incremental` |
| Settings (4) | GET `/setting/`; GET/PUT/DELETE `/setting/{key}` | `config setting list/show/set/remove` |
| Health (3) | GET `/health/`, `/health/live`, `/health/ready` | `service health`; no CLI bindings for probes |
| Snapshots (3) | POST `/config/save`, `/config/load`, `/config/validate` | `config save/load/validate` |
| Lifecycle (3) | POST `/service/start`, `/service/stop`; GET `/service/log` | `service start/stop/log` |

These are the **29** target operations. Use [CLI reference](CLI.md) for flags and `/openapi.json` for complete schemas, operation IDs, status codes and examples.

### Profiles and persistence upgrades

Say-specific defaults are no longer top-level fields:

```json
{
  "name": "default",
  "is_active": true,
  "shared": {},
  "modules": {
    "opal-say": {"voice_id": "amy", "speed": 1.0, "volume": 0.9, "output_format": "wav"},
    "another-service": {"preserved": [1, 2]}
  }
}
```

Create accepts a portable document and retains foreign compartments. Update may change the Say compartment but cannot rewrite shared or foreign compartments. Own fields merge; null removes an override except that `sample_rate: null` is a stored instruction to let the service choose. `is_active: false` does not deactivate the current profile; activate another profile instead. Deleting the active profile is a conflict.

The SQLite upgrade moves old flat defaults into `modules.opal-say` and preserves numeric IDs as their string representations. It also permits unbound personas. Upgrades are transactional: a failure leaves the old records/schema recoverable for retry. Back up the database through normal stopped-service filesystem procedures before production upgrades; tests use disposable previous-format fixtures, not your live data.

### Personas and optional ownership

`voice_id` may be absent or null on a persona. `origin` reports local/owner provenance. Generation accepts canonical `persona_id` and the retained deprecated `persona` alias; conflicting values are invalid. A persona without a usable binding cannot supply a synthesis voice, though an explicit request voice may override it.

With a persona owner configured, its same-ID definition wins and its ETag is used for conditional refresh. Local records remain stored separately from the owner cache. The service can operate with no owner; offline cache/local fallback does not require a running sibling module. Persona validation reports unusable bindings without generating audio.

### Registered settings

`GET /setting/` returns typed effective values, including registered defaults. An item read/write reports effective value, configured override, default, provenance, override flags, restart state and masking warnings. The environment wins over a persisted override without changing it. DELETE resets an override idempotently; it does not remove the setting definition. Unknown setting writes are rejected.

Important defaults include `max_text_chars=20000`, `max_audio_bytes=134217728`, `chunk_max_chars=400`, `default_speed=1.0`, `default_volume=1.0`, and `default_output_format=wav`. Streaming is governed by text/runtime backpressure limits rather than the completed-response byte limit. Registered runtime settings also cover acquisition timeout, stream buffering/lag, incremental pending chunks and persona refresh.

### Health report and probes

`GET /health/` contains service identity, `status`, `live`, `ready`, active profile and `details.engines`. It returns 200 whenever a report can be produced, even when generation is unready. `/health/live` is a cheap machine liveness probe. `/health/ready` returns 200 or 503 according to the usable default speech path; it does not require a synthesis warmup. Check model files, voice enablement, profile defaults and engine availability when readiness fails.

### Configuration snapshots

The envelope has `format_version: "1.0.0"`, `module: "opal-say"`, `created_at`, native-JSON `settings`, and `resources` containing `profiles`, `personas`, `voices`, and `engines`. It contains desired configuration only: no credentials, model/media files, generation history or runtime caches.

| Operation | Behavior |
| --- | --- |
| Save omitted body or `{}` | Atomically replace the managed snapshot; no live revision change |
| Save `{"destination":"response"}` | Return a snapshot without changing the managed one |
| Validate omitted body or `{}` | Validate current live configuration, not the managed snapshot |
| Validate `{"snapshot":{...}}` | Validate the supplied candidate without any mutation |
| Load omitted body or `{}` | Apply the managed snapshot with mandatory `If-Match` |
| Load `{"snapshot":{...}}` | Apply the supplied snapshot without rewriting the managed one |

Explicit null candidates are invalid. Validation returns 200 with `valid`, `errors` and `warnings`, even for an invalid candidate. Load rejects invalid/wrong-module/unsupported-version/unknown-section input and applies the complete configuration atomically, not as a merge. Omitted settings overrides and collections are cleared. Success advances the revision once; failure preserves stored data, revision, active selection and runtime configuration. Managed snapshot replacement uses an atomic filesystem rename so a failed save retains the previous snapshot.

Foreign profile compartments round-trip. Unknown namespaces are preserved and warned about, not interpreted as credentials or runtime state. Host model assets must be installed separately after moving a snapshot to another machine.

## Audio and integrations

Completed `/generate` returns WAV or raw PCM. The two stream operations always return mono S16LE PCM. Read `X-Opal-Audio-Format`, `X-Opal-Sample-Rate`, `X-Opal-Channels`, `X-Opal-Sample-Format`, `X-Opal-Engine-Id`, `X-Opal-Voice-Id` and optional profile/persona metadata rather than assuming a rate.

Incremental input is NDJSON: one configuration record, text/flush records, then `{"done":true}`. Text can arrive while audio is already being produced. A failure before audio starts is an ordinary error response. Once bytes start, failures terminate the stream; receivers must treat truncated output as incomplete.

Use bounded producers/consumers and handle cancellation and engine acquisition timeout. Engine configuration changes preserve already-bound synthesis; snapshots prepare replacement configurations before publication and roll back on failed application.

## Web console

```bash
npm --prefix spa install
npm --prefix spa run build
```

Restart the service if necessary, then open `/ui/`. Development uses Vite on port 5173. The console exposes generation/playback, voices, profiles, settings, health, lifecycle and configuration snapshots. Its Configuration tab saves/downloads JSON, opens candidate JSON, validates live/candidate state and loads managed/candidate snapshots. Load asks for confirmation. Enter a bearer credential when required; it is not stored in local/session storage or snapshots.

## Verification

```bash
python tools/bundle_contract.py --check
python -m pytest
python -m ruff check src tests
npm --prefix spa test
npm --prefix spa run typecheck
npm --prefix spa run build
```

Optional real-adapter tests skip when their runtime/models are absent. Deterministic adapters cover service/HTTP/SQLite semantics; they are not audible-playback evidence. See the [cookbook](COOKBOOK.md) for integration patterns.

## Caller cutover

Retired info routes and the `info` CLI group are replaced by health reporting/probes and `service health`. Retired backup/import/export/restore routes and commands are replaced by snapshot save/load/validate; no transition shim is provided. Retired engine/persona routes and CLI groups are replaced by `resources.engines`/`resources.personas` snapshot sections and persona selection on generation requests. Flat profiles and integer-only callers must move to namespaced documents and string IDs. Settings collection PATCH and old flat CLI aliases are gone. Preserve the target-defined persona selector alias while migrating producers to `persona_id`.
