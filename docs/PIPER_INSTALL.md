# Installing Piper for OPAL Say

Piper is the recommended first engine: small ONNX voices, fast on CPU, and happy on a
Raspberry Pi. OPAL Say wraps the [`piper-tts`](https://pypi.org/project/piper-tts/)
Python package — **the runtime and the voice models are separate installs**. The service
never downloads or manages model files; this guide installs them in this checkout's
`resources/piper/` directory and points the engine at those files.

## 1. Install the runtime

Requires Python ≥ 3.11 (the wheels ship for Linux x86_64/aarch64, Windows, and macOS).

```bash
# from the repo checkout
pip install -e '.[piper]'

# or, for a released package
pip install 'opal-say[piper]'
```

Both engines at once: `pip install -e '.[engines]'`.

### GPU (optional)

Piper runs well on CPU. For CUDA hosts, install the GPU build of ONNX Runtime
(`pip install onnxruntime-gpu`, replacing the CPU wheel) and set `use_cuda: true`
in the engine options below.

## 2. Install voice models

A Piper voice is **two files that must sit side by side**: `NAME.onnx` (the model) and
`NAME.onnx.json` (its config). Browse voices at
<https://huggingface.co/rhasspy/piper-voices> — quality tiers are `low`, `medium`, and
`high` (bigger model, better fidelity, slower synthesis).

```bash
# Linux / macOS / Git Bash
mkdir -p ~/piper/voices && cd ~/piper/voices
curl -fLO https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low/en_US-amy-low.onnx
curl -fLO https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low/en_US-amy-low.onnx.json
```

```powershell
# Windows PowerShell
mkdir ~\piper\voices
curl.exe -fLo ~\piper\voices\en_US-amy-low.onnx `
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low/en_US-amy-low.onnx
curl.exe -fLo ~\piper\voices\en_US-amy-low.onnx.json `
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low/en_US-amy-low.onnx.json
```

The **voice reference** is the model basename without extension — `en_US-amy-low` here.
Keep both files together; a `.onnx` without its `.onnx.json` won't validate.

## 3. Point the engine at the voices

When the service is started from the repository root, set `voices_dir` to the
repository-relative location in `config/engines.yaml` (and, if you use its initial engine
configuration, `config/service.yaml`):

```yaml
engines:
  piper:
    enabled: true
    options:
      voices_dir: resources/piper   # flat dir; '~' expands, absolute paths work too
      use_cuda: false
      cache_size: 4                 # loaded models kept in memory (1..32)
```

Relative engine paths are resolved from the service process's working directory. If the
service runs from another directory (for example, under a service manager), use an
absolute path — the safest way is to update the running service (persisted in SQLite):

```bash
# Run from the repository root.
REPO_ROOT="$(pwd)"
opal say engines update piper -O "voices_dir=$REPO_ROOT/resources/piper"
opal say engines update piper --enable
```

```powershell
# Windows PowerShell; run from the repository root.
$repoRoot = (Get-Location).Path
opal say engines update piper -O "voices_dir=$repoRootesources\piper"
opal say engines update piper --enable
```

If you edit the YAML files after the service has already started, apply them with
`opal say backup import`; otherwise the persisted engine configuration continues to win.

## 4. Verify

```bash
opal say engines validate piper          # runtime + options checks
opal say voices add amy --name Amy --engine piper --ref en_US-amy-low
opal say voices validate amy             # model + config resolve
opal say voices test amy -o test.wav     # synthesizes a short sample
opal say generate "Piper is installed." -o hello.wav
```

With the whole catalog installed, register every resolvable model in one loop (ids are
the model basenames):

```bash
for onnx in resources/piper/*.onnx; do
  ref="$(basename "${onnx%.onnx}")"
  opal say voices add "$ref" --name "$ref" --engine piper --ref "$ref" || true
done
opal say voices list --engine piper | wc -l
```

`voices_dir` is optional: a voice reference may also be an **absolute path** to a
`.onnx` file — useful for one-off or shared models outside the voices directory. If the
configured `voices_dir` doesn't exist, the engine stays available but warns that only
absolute references resolve.

## Voice references and options

| Where | Option | Meaning |
|---|---|---|
| engine | `voices_dir` | Directory used to resolve voice refs by name (`en_US-amy-low` → `<dir>/en_US-amy-low.onnx`) |
| engine | `use_cuda` | Use the CUDA execution provider (needs `onnxruntime-gpu`) |
| engine | `cache_size` | Number of loaded voices kept resident (default 4, LRU) |
| voice | `config_path` | Explicit `.onnx.json` path when the config isn't beside the model |
| generation | `speaker` / `speaker_id` | Pick a speaker on multi-speaker models (`-O speaker=speaker_2`) |

Engine options go on the engine (`engines update`) or a voice; generation options go on
profiles or per-request: `opal say generate "…" -O speaker=alan`.

## Raspberry Pi notes

- Use `low`-quality voices — `medium`/`high` models are noticeably slower and memory-hungry.
- aarch64 wheels of `piper-tts`/`onnxruntime` install normally on 64-bit Raspberry Pi OS;
  on unusual distros you may need to build ONNX Runtime or use a Debian container.
- First synthesis of a voice loads the model; warm it with `voices test` or keep
  `cache_size` low on 1 GB boards.

## Troubleshooting

| Symptom | Check |
|---|---|
| `engines validate` says runtime unavailable | `pip install 'opal-say[piper]'` in the service's environment |
| voice validation: model not found | ref must be a basename in `voices_dir` or an absolute `.onnx` path |
| voice validation: config missing | the `NAME.onnx.json` file must sit beside `NAME.onnx` (or set `config_path`) |
| warning `voices_dir_missing` | path typo — `~` is expanded, relative paths resolve against the service's working directory |
| CUDA requested but ignored | install `onnxruntime-gpu`; `onnxruntime.get_available_providers()` should list `CUDAExecutionProvider` |

## Licensing

`piper-tts` and `onnxruntime` are MIT, but **each voice model carries its own license**
in `rhasspy/piper-voices` (ranging from permissive/CC-BY to non-commercial), and
`piper-tts` bundles espeak-ng-derived components licensed GPL-3.0 — relevant if you
redistribute engine binaries. See the *License implications* section of the README
before commercial use.
