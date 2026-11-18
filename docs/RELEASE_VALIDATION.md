# Real-engine release validation

`tests/test_release_validation.py` verifies OPAL Say 1.7.1 against actual
Piper and Kokoro engines: engine availability and model initialization, WAV
generation, PCM streaming, output format headers, model caching across
requests, configuration updates between requests, and performance measurement
(time to first audio, total duration, allocation peak).

These tests are skipped automatically when the runtime or model files are
missing, so they never run — or fail — in standard CI
(`pytest -m "not piper and not kokoro"`). No models are downloaded by CI.

## Prerequisites

```sh
pip install -e '.[piper]'     # piper-tts + onnxruntime
pip install -e '.[kokoro]'    # kokoro-onnx
pip install -e '.[engines]'   # both
```

## Model files

| Variable | Meaning | Default |
|---|---|---|
| `OPAL_SAY_TEST_PIPER_MODEL` | Path to a Piper voice `.onnx`; its `.onnx.json` config must sit alongside | `~/piper/voices/en_US-amy-low.onnx` |
| `OPAL_SAY_TEST_KOKORO_DIR` | Directory containing `kokoro-v1.0*.onnx` and `voices-v1.0.bin` | `~/kokoro` |

Piper voices are published on the `rhasspy/piper-voices` Hugging Face repo —
download the `.onnx` and `.onnx.json` for one voice (e.g. `en_US-amy-low`).
Kokoro release archives provide `kokoro-v1.0.onnx` and `voices-v1.0.bin`.

## Running

```sh
# Linux / macOS
OPAL_SAY_TEST_PIPER_MODEL=~/piper/voices/en_US-amy-low.onnx pytest -m piper
OPAL_SAY_TEST_KOKORO_DIR=~/kokoro pytest -m kokoro
pytest -m "piper or kokoro"        # whichever is available
pytest tests/test_release_validation.py -v -s   # -s prints the measurements
```

```powershell
# Windows (PowerShell)
$env:OPAL_SAY_TEST_PIPER_MODEL="$HOME\piper\voices\en_US-amy-low.onnx"
pytest -m piper
$env:OPAL_SAY_TEST_KOKORO_DIR="$HOME\kokoro"
pytest -m kokoro
```

```sh
# Raspberry Pi (aarch64): piper-tts publishes ARM wheels; prefer a
# `low`-quality voice. Kokoro is practical only on 64-bit systems.
OPAL_SAY_TEST_PIPER_MODEL=~/piper/voices/en_US-amy-low.onnx pytest -m piper
```

Timing and memory figures are printed for the operator's review (`-s`);
assertions stay relative and hardware-independent on purpose.

## Standard CI

Regular push/PR runs use only the fake engine:

```sh
pytest -m "not piper and not kokoro"
```

The `release-validation` workflow job is `workflow_dispatch`-only: it downloads
the small public `en_US-amy-low` Piper voice and runs `-m piper` on demand.
It never runs on push or pull requests.

## OPAL Event handoff

`opal-event`'s `say_request` plugin dispatches `say.request` events to a Say
endpoint and verifies the WAV it gets back. Its smoke test runs two ways
(from the `opal-event` checkout):

```sh
# Fake engine — no live service needed; runs in normal CI
pytest tests/integration/test_event_say_smoke.py

# Live — a real opal-say with a real engine; verifies event id, execution
# receipt, terminal success, and a nonempty playable WAV
OPAL_SAY_URL=http://127.0.0.1:8002 \
OPAL_SAY_VOICE=en-us-amy-low OPAL_SAY_ENGINE=piper \
pytest tests/integration/test_event_say_smoke.py
```

To stand up the live target (Windows `.venv` interpreter shown; use
`.venv/bin/python` elsewhere):

```powershell
$env:OPAL_SAY_PORT = "8002"
$env:OPAL_SAY_DATA_DIR = ".\var-live"
.venv\Scripts\python -m opal_say          # serving on 127.0.0.1:8002
```

`GET /api/v1/health/ready` must return `200` (a working default speech path,
not merely an installed engine) before the live smoke is meaningful — e.g.
register the bundled `resources/piper/en_US-amy-low.onnx` voice, set the
profile active, then run the test.
