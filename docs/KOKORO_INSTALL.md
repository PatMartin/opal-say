# Installing Kokoro for OPAL Say

Kokoro-82M is the higher-fidelity engine: expressive, multi-lingual voices that still run
comfortably on CPU. OPAL Say wraps the [`kokoro-onnx`](https://github.com/thewh1teagle/kokoro-onnx)
runtime — **the runtime and the model files are separate installs**. The service never
downloads or manages model files; you place them on the host and point the engine at them.

## 1. Install the runtime

Requires Python ≥ 3.11.

```bash
# from the repo checkout
pip install -e '.[kokoro]'

# or, for a released package
pip install 'opal-say[kokoro]'
```

Both engines at once: `pip install -e '.[engines]'`. Kokoro synthesis runs on CPU via
ONNX Runtime — no GPU setup is needed.

## 2. Download the model files

Kokoro needs **two files** from the
[kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases)
(tag `model-files-v1.0`):

| File | Purpose |
|---|---|
| `kokoro-v1.0.onnx` | Full-precision model (~330 MB) — best fidelity |
| `kokoro-v1.0.int8.onnx` | Int8-quantized model (~90 MB) — smaller, faster, near-identical for most uses |
| `voices-v1.0.bin` | The voice pack — **all** voices live in this one file |

```bash
# Linux / macOS / Git Bash
mkdir -p ~/kokoro && cd ~/kokoro
curl -fLO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx
curl -fLO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

```powershell
# Windows PowerShell
mkdir ~\kokoro
curl.exe -fLo ~\kokoro\kokoro-v1.0.int8.onnx `
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx
curl.exe -fLo ~\kokoro\voices-v1.0.bin `
  https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

Pick **one** model file — the int8 build is the sensible default; use the full-precision
`kokoro-v1.0.onnx` where fidelity matters more than size.

## 3. Point the engine at the files

`config/service.yaml` already contains the conventional locations:

```yaml
engines:
  kokoro:
    enabled: true
    options:
      model_path: ~/kokoro/kokoro-v1.0.int8.onnx   # '~' is expanded; absolute paths work too
      voices_path: ~/kokoro/voices-v1.0.bin
      default_lang: en-us
```

Or update the running service instead of the file (persisted in SQLite):

```bash
opal say engines update kokoro -O model_path=~/kokoro/kokoro-v1.0.int8.onnx \
                                -O voices_path=~/kokoro/voices-v1.0.bin \
                                -O default_lang=en-us
opal say engines update kokoro --enable
```

Both paths are **required** — the engine reports unavailable until they point at real files.

## 4. Verify

```bash
opal say engines validate kokoro         # runtime + model/voices file checks
opal say voices add heart --name Heart --engine kokoro --ref af_heart
opal say voices validate heart
opal say voices test heart               # synthesizes a short sample
opal say generate "Kokoro is installed." -o hello.wav
```

## Voice references and options

A voice reference is a **name inside `voices-v1.0.bin`** — there is no per-voice model
file. Names encode language and gender by prefix:

| Prefix | Language | Example voices |
|---|---|---|
| `a` | `en-us` | `af_heart`, `af_bella`, `am_adam`, `am_michael` |
| `b` | `en-gb` | `bf_emma`, `bm_george` |
| `e` | `es` | `ef_dora` |
| `f` | `fr-fr` | `ff_siwis` |
| `h` | `hi` | `hf_alpha` |
| `i` | `it` | `if_sara` |
| `j` | `ja` | `jf_alpha` |
| `p` | `pt-br` | `pf_dora` |
| `z` | `cmn` (Mandarin) | `zf_xiaobei` |

Second letter `f`/`m` marks female/male. The shipped seed catalog registers
`kokoro-af-heart` and `kokoro-bm-george` as examples; any name in the voices file works —
the full list is on the kokoro-onnx release page.

| Where | Option | Meaning |
|---|---|---|
| engine | `model_path` | Path to `kokoro-v1.0.onnx` or `kokoro-v1.0.int8.onnx` |
| engine | `voices_path` | Path to `voices-v1.0.bin` |
| engine | `default_lang` | Fallback language when the voice prefix doesn't imply one |
| generation | `lang` | Per-request language override — one of `en-us`, `en-gb`, `es`, `fr-fr`, `hi`, `it`, `ja`, `pt-br`, `cmn` |

Example: `opal say generate "…" -O lang=fr-fr`.

## Constraints worth knowing

- **Sample rate** — Kokoro synthesizes natively at 24000 Hz; the service resamples when
  you request another `--sample-rate`.
- **Speed range** — `0.5`–`2.0` (narrower than the contract's `0.25`–`4.0`); requests
  outside that range are rejected with `unsupported_speed`.
- **CPU is the target** — the int8 model streams comfortably on a desktop or recent
  laptop; on Raspberry Pi expect usable but slower synthesis (keep chunks short).

## Troubleshooting

| Symptom | Check |
|---|---|
| "kokoro-onnx runtime is not installed" | `pip install 'opal-say[kokoro]'` in the service's environment |
| "options 'model_path' and 'voices_path' must be configured" | set both engine options — neither is optional |
| "model file '…' not found" | `model_path` typo — point at the `.onnx` you actually downloaded (int8 vs full) |
| "voices file '…' not found" | `voices_path` typo — must be `voices-v1.0.bin` |
| voice validation: unknown voice | the ref must be a name inside the voices file (`af_heart`), not a path |
| `unsupported_speed` error | keep speed within `0.5`–`2.0` |

## Licensing

`kokoro-onnx` and `onnxruntime` are MIT, and the Kokoro-82M weights are Apache-2.0, but
**each voice inside `voices-v1.0.bin` carries its own license** recorded upstream — some
derive from datasets that require attribution or restrict commercial use. See the
*License implications* section of the README before commercial use.
