# OPAL Talk

**Implementation-neutral text-to-speech for the OPAL suite.** One authoritative REST contract, three interchangeable clients, and pluggable speech engines — so the same API surface works whether the backend is Piper on a Raspberry Pi or Kokoro on a GPU host.

- **`opal-talk-svc`** — FastAPI service implementing every contract operation: WAV and raw-PCM output, progressive streaming, internal text chunking, bounded inference admission, and a reference-counted model cache. Ships with working **Piper** (`piper-tts`) and **Kokoro** (`kokoro-onnx`) adapters; more engines can register via the `opal_talk.engines` entry-point group.
- **`opal talk …`** — Typer CLI in which every command is a thin HTTP client of the REST API (`opal-talk …` is an equivalent entry point).
- **SPA** — React + Vite + Vitest UI in [spa/](spa/) that talks only to the REST API and is served at `/ui` when built.

No audio playback, no engine lock-in, no hidden surface: clients can only do what the contract declares.

## Contract

The implemented API is **v1.2.0**. The single authoritative copy is the packaged document [`src/opal_talk/contract/opal_talk.yaml`](src/opal_talk/contract/opal_talk.yaml): it is loaded by `opal_talk.contract.load_contract()`, served by the service at `/openapi.json`, and used by the test suite to validate every response.

> **Next revision in flight.** [`opal_talk_v1.4.0.yaml`](opal_talk_v1.4.0.yaml) sits at the repo root as the incoming contract. Its delta over 1.2.0 is small but semantic: settings values become native JSON (instead of strings), and `POST /api/v1/backup/create` returns `201` with a `Location` header (instead of `200`). It is **not yet implemented** — the code, version metadata, and packaged contract still describe 1.2.0. Interpretations of under-specified details are recorded in [docs/CONTRACT_NOTES.md](docs/CONTRACT_NOTES.md).

Parity is enforced by tests, not convention: every contract operation has a route with the same operationId/method/path, every suite response is schema-validated against the contract, the CLI and SPA operation maps equal the contract's, every operation has an exercised CLI command, and every operation is used by the SPA.

## Highlights

- **Three clients, one API** — REST, CLI, and SPA never diverge because the contract is the source of truth for all of them.
- **Progressive streaming** — `POST /api/v1/generate/stream` emits raw S16LE mono PCM as it is synthesized; `x-opal-sample-rate`, `x-opal-channels`, and `x-opal-sample-format` headers describe the wire format. Errors before headers return a JSON error envelope; errors after headers truncate the stream.
- **Bounded inference** — engines declare a concurrency cap (`max_concurrent_inferences`, default 1); requests queue on a bounded semaphore and excess load fails fast with `503 engine_unavailable` instead of overloading the host.
- **Safe reconfiguration** — runtime configuration updates create generations: in-flight requests finish on their bound generation, updates apply deterministically on drain, and waiters are never orphaned or interrupted mid-synthesis.
- **Bounded model cache** — LRU-capped, single-flight model loading with reference-counted entries; evicted models are disposed exactly once, even after same-key replacement.
- **Host-level operations** — health probes (`/healthz`, `/api/v1/health/live`, `/api/v1/health/ready`), engine/voice validation, settings management, and full backup create/restore/export/import/validate.

## Architecture

```
src/opal_talk/
  api/            REST layer — routes use the contract's operationIds; /openapi.json serves the contract
  service/        application layer: catalog (engines/voices/profiles), generation, settings,
                  config/backup, error mapping, bounded-concurrency helpers
  tts/            engine-neutral core — TtsEngine interface, chunking, resampling/gain/WAV encoding,
                  bounded LRU model cache, runtime manager, registry
    piper_adapter.py    Piper (piper-tts)
    kokoro_adapter.py   Kokoro (kokoro-onnx)
  persistence/    SQLite store
  cli/            HTTP client + Typer commands
  contract/       packaged opal_talk.yaml (the served contract)
  config.py       YAML service configuration
spa/              React + Vite + Vitest
config/           service.yaml and importable engines/voices/profiles/settings YAML
```

Adapters implement `TtsEngine` (`availability`, `describe_voice`, `synthesize`, declarative `option_specs`). The core handles chunking, sample-rate conversion, volume, and encoding, so engine-specific behavior stays in the adapter and reaches requests only through opaque `engine_options`.

## Install

Requires **Python ≥ 3.11** and, for the SPA, **Node ≥ 18**.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[engines,dev]'   # or '.[piper]' / '.[kokoro]' / '.' for core only
(cd spa && npm install && npm run build)    # optional: served at /ui
```

On Windows the same commands work in Git Bash (the Makefile handles `.venv/Scripts` paths); `make install` bootstraps everything.

## Engines

OPAL Talk ships two adapters. Engine runtimes and models are **host-installed and never uploaded** — the service only stores references to files on the machine it runs on.

### Piper (`piper-tts`)

Lightweight ONNX voices for local, low-latency synthesis — the practical choice for CPU-only hosts and Raspberry Pi.

```bash
# runtime
pip install -e '.[piper]'

# voices: pick from https://huggingface.co/rhasspy/piper-voices —
# each voice is a *.onnx model plus its *.onnx.json config, e.g.
mkdir -p ~/piper/voices && cd ~/piper/voices
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/low/en_US-amy-low.onnx
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/low/en_US-amy-low.onnx.json
```

Point the engine at the directory via `config/service.yaml` (`engines.piper.options.voices_dir`) or at runtime:

```bash
opal talk engines update piper --option voices_dir=~/piper/voices
opal talk engines validate piper
opal talk voices add amy --name Amy --engine piper --ref en_US-amy-low
opal talk voices test amy
opal talk profiles add main --voice amy --active
opal talk generate "Hello from OPAL Talk." -o hello.wav
```

`--ref` is the voice name (the `.onnx` basename) when `voices_dir` is set, or an absolute model path. Per-request tweaks go through `engine_options`, e.g. `opal talk generate "…" -O noise_scale=0.667 -O length_scale=1.2`. On CUDA hosts, install `onnxruntime-gpu` and set `use_cuda: true`.

### Kokoro (`kokoro-onnx`)

Kokoro-82M ONNX: higher-quality multi-voice synthesis, still CPU-friendly, with an int8-quantized model for constrained hosts.

```bash
# runtime
pip install -e '.[kokoro]'

# model + voices: from https://github.com/thewh1teagle/kokoro-onnx/releases
mkdir -p ~/kokoro && cd ~/kokoro
curl -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx
curl -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

```bash
opal talk engines update kokoro --option model_path=~/kokoro/kokoro-v1.0.int8.onnx \
                                --option voices_path=~/kokoro/voices-v1.0.bin \
                                --option default_lang=en-us
opal talk engines validate kokoro
opal talk voices add heart --name Heart --engine kokoro --ref af_heart
opal talk voices test heart
opal talk profiles add alt --voice heart --active
opal talk generate "Kokoro speaks too." -o heart.wav
```

`--ref` is a Kokoro voice name from `voices-v1.0.bin` (`af_heart`, `af_bella`, `bm_george`, …). Use the full-precision `kokoro-v1.0.onnx` where fidelity matters more than size.

### License implications

Engine availability ≠ freedom to redistribute. Before shipping or offering this publicly, check **three layers** per engine:

- **Adapter/runtime code** — `piper-tts` and `kokoro-onnx` are MIT-licensed Python packages; `onnxruntime` is MIT. These pose no restriction on use or redistribution.
- **Model weights & voices** — this is the layer that varies. Kokoro-82M weights are Apache-2.0, but the bundled voices in `voices-v1.0.bin` carry **per-voice licenses** recorded in the upstream project; some are derived from datasets that require attribution or restrict commercial use. Piper voices come from many different training datasets — each voice directory in `rhasspy/piper-voices` carries its own license, ranging from permissive/CC-BY to **non-commercial**. Check the license for each voice you deploy; it applies to generated audio used commercially as well.
- **Bundled components** — `piper-tts` incorporates espeak-ng-derived phonemization components licensed **GPL-3.0**. Using the engine as a service in your own process is generally fine, but redistributing the engine binaries (e.g., shipping an appliance image) may trigger GPL obligations — consult counsel if that's your deployment model.

Practical rule: the service's MIT-style engine integrations are safe; **audit each voice/model file individually** before commercial use, keep attribution where required, and don't assume "downloadable" means "redistributable."

## Configure

- [`config/service.yaml`](config/service.yaml) — host/port (default `127.0.0.1:8001`), `data_dir` (SQLite + backups, default `./var`), `config_dir`, CORS origins, SPA dir, and initial engine options. Override the file with `OPAL_TALK_CONFIG`; override fields with `OPAL_TALK_HOST`, `OPAL_TALK_PORT`, `OPAL_TALK_DATA_DIR`, `OPAL_TALK_CONFIG_DIR`, `OPAL_TALK_SPA_DIR`.
- `config/{engines,voices,profiles,settings}.yaml` — imported on first start and via `POST /api/v1/backup/import` (`opal talk backup import`). Only present files replace their section; everything is validated before anything is applied.
- Settings (`opal talk settings set KEY VALUE`):

| Key | Default | Meaning |
|---|---|---|
| `max_text_chars` | 20000 | Maximum accepted text length (413 above) |
| `max_audio_bytes` | 128 MiB | Maximum **encoded** response body for non-streaming generate, WAV container included |
| `chunk_max_chars` | 400 | Internal text-chunk size for synthesis/streaming |
| `default_speed` | 1.0 | Service default speed when no profile/request value applies (0.25–4.0) |
| `default_volume` | 1.0 | Service default volume (0.0–2.0) |
| `default_output_format` | `wav` | `wav` \| `pcm` |

Streaming responses are governed by text and runtime resource limits, not `max_audio_bytes`.

## Run

```bash
.venv/bin/opal-talk-svc                         # http://127.0.0.1:8001 — docs at /docs, SPA at /ui
export OPAL_TALK_API_URL=http://127.0.0.1:8001  # CLI default

opal talk health
opal talk engines list
opal talk voices add lessac --name Lessac --engine piper --ref en_US-lessac-medium
opal talk profiles add main --voice lessac --active
opal talk generate "Hello from OPAL Talk." -o hello.wav
opal talk generate "Streamed speech." --stream -o - | aplay -r 22050 -f S16_LE -c 1
opal talk backup create
```

Global options go before the command group: `opal-talk --json voices list` prints raw JSON; `-O key=value` / `--options-json` pass `engine_options`. The complete CLI reference is [docs/CLI.md](docs/CLI.md); the [User Guide](docs/USER_GUIDE.md) covers installation, architecture, configuration, the REST API, streaming, the web GUI, and integration.

## API surface

All operations live under `/api/v1` and carry `X-Opal-*` correlation headers (session, transaction, request, causation), with errors in the uniform `OpalError` envelope.

- **engines** — list/get/reconfigure engines and options, `parameters`, `validate`
- **voices** — CRUD, `parameters`, `validate`, `test`
- **profiles** — CRUD, `activate`
- **generate** — `POST /api/v1/generate` (WAV/PCM file or JSON-encoded audio) and `POST /api/v1/generate/stream` (raw PCM stream)
- **settings** — list/get/put/patch/delete
- **backup** — list/create/download/restore/delete/export/import/validate
- **health** — service health, plus `/healthz`, `/api/v1/health/live`, `/api/v1/health/ready`

## Development

```bash
make test        # Python: unit, contract, adapter (real Piper/Kokoro when present), CLI, integration, streaming
make test-fast   # without real engines — this is what standard CI runs
make test-spa    # Vitest + typecheck
make lint        # ruff
make spa-dev     # Vite dev server on :5173 against the service on :8001
```

- **Real-engine tests** look for `~/piper/voices/en_US-amy-low.onnx` and `~/kokoro/` (override with `OPAL_TALK_TEST_PIPER_MODEL`, `OPAL_TALK_TEST_KOKORO_DIR`) and are skipped — never failed — when absent. See [docs/PERFORMANCE_TESTS.md](docs/PERFORMANCE_TESTS.md) for CPU/GPU/Raspberry Pi notes and [docs/RELEASE_VALIDATION.md](docs/RELEASE_VALIDATION.md) for the real-engine release checklist.
- **CI** runs Python 3.11/3.13 on Linux and Windows, builds and validates the wheel (checking package metadata ↔ `__version__` ↔ contract `info.version` consistency), runs the SPA suite, and keeps all real-engine work behind a manual-dispatch job — no model downloads on push/PR.
