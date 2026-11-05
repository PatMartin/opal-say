![OPAL Talk — a voice for the OPAL ecosystem](opal-talk.png)

# OPAL Talk
### Turn ordinary text into a voice your projects can actually use.

**Your camera sees someone at the door. Your assistant knows what to say. Now it needs a voice.**

OPAL Talk is the **text-to-speech engine of [OPAL](https://github.com/PatMartin/opal)**: send text to a small REST service, choose a voice, and get real audio back—either a ready-to-use WAV file or **progressive PCM audio** that starts arriving while the rest of the sentence is still being synthesized.

It provides one consistent interface over local synthesis engines, including **Piper** and **Kokoro**. Change the engine, swap the voice, tune the delivery, and keep the same API.

~~~text
     "Someone is at the front door."
                       │
                       ▼
                  OPAL Talk
                       │
            REST API (FastAPI)
                       │
         ┌─────────────┴─────────────┐
         ▼                           ▼
     Piper engine               Kokoro engine
  efficient, local TTS      expressive, local TTS
         └─────────────┬─────────────┘
                       ▼
            Voice + profile settings
                 ┌─────┴─────┐
                 ▼           ▼
             Finished     Progressive
             WAV / PCM     PCM stream
                 │           │
                 └─────┬─────┘
                       ▼
             Speakers, apps, files,
             alerts, or other services
~~~

> **Important distinction:** OPAL Talk **generates audio; it does not play it**. A shell player such as `aplay`, your own application, or the browser provides playback. This separation lets the same service power a Raspberry Pi speaker, a dashboard, a phone system, and an automation pipeline.

## What makes it interesting?

- **One API, multiple voices.** Piper and Kokoro adapters expose a common generation contract, including speed, volume, audio format, and output sample rate.
- **Local-first.** Synthesis runs with engines and models installed on your own host. Basic operation doesn't require a cloud speech service.
- **Actually streaming.** `generate --stream` emits raw 16-bit mono PCM progressively, rather than waiting for the whole finished WAV.
- **Voices are resources.** Register installed voices once, validate them, and refer to stable IDs from scripts and profiles.
- **Profiles make speech contextual.** Give a home assistant a friendly voice, an announcement system a distinct one, or a phone line 8 kHz PCM.
- **Tune without changing applications.** Adjust engine-specific options, defaults, sentence chunking, and per-request overrides through REST or Typer.
- **Three consistent interfaces.** FastAPI REST, a complete Typer CLI (`opal talk ...`), and a React web console all use the same backend.
- **Extensible engines.** New TTS implementations can be registered through the `opal_talk.engines` Python entry-point group.
- **Inspectable by design.** Health checks, engine/voice validation, predictable errors, configuration export, and tests make voice infrastructure easier to operate.

OPAL Talk complements [OPAL Listen](https://github.com/PatMartin/opal-listen), which understands speech, and [OPAL Event](https://github.com/PatMartin/opal-event), which routes events and executes actions. **Listen hears. Event reacts. Talk speaks.**

## Installation

### 1. Install the project

Requires **Python 3.11+**. Linux and Raspberry Pi OS are natural targets; macOS can also run the Python service with supported engine dependencies. Node.js **18+** is needed only for the optional web UI.

~~~bash
git clone https://github.com/PatMartin/opal-talk.git
cd opal-talk

python3 -m venv .venv
source .venv/bin/activate

# Both speech engines plus developer/testing tools:
pip install -e '.[engines,dev]'

# Alternatives:
# pip install -e '.[piper]'      # Piper only
# pip install -e '.[kokoro]'     # Kokoro only
# pip install -e .              # API/CLI core, no speech engine runtime
~~~

The package installs three commands:

| Command | What it does |
| --- | --- |
| `opal-talk-svc` | Starts the REST service |
| `opal talk ...` | OPAL Talk's Typer CLI beneath the shared `opal` root |
| `opal-talk ...` | Standalone shortcut to the same Talk CLI |

If you install multiple OPAL packages in the same Python environment, they may each install an `opal` entry point. The last installed script can win. **Use `opal-talk` in automation scripts** when you want an unambiguous executable; the examples below use `opal talk` for consistency across OPAL.

### 2. Install at least one voice model

**The Python engine package and its speech model are separate things.** OPAL Talk does **not** download, upload, or delete voice model files for you. Install those files on the **service host**.

#### Piper: a great first voice on edge hardware

A Piper voice needs two matching files: `*.onnx` and `*.onnx.json`. The default service configuration expects them under `~/piper/voices`.

~~~bash
mkdir -p ~/piper/voices

curl -fL \
  'https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low/en_US-amy-low.onnx' \
  -o ~/piper/voices/en_US-amy-low.onnx

curl -fL \
  'https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/low/en_US-amy-low.onnx.json' \
  -o ~/piper/voices/en_US-amy-low.onnx.json
~~~

The seeded voice catalog calls this voice `en-us-amy-low` and refers to the underlying Piper model as `en_US-amy-low`.

More models: [Piper voices](https://huggingface.co/rhasspy/piper-voices).

#### Kokoro: another voice with a different character

Kokoro requires a model and a voices file. The default engine configuration expects an INT8 model and a shared voices file under `~/kokoro`. Download them from the [kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases):

~~~bash
mkdir -p ~/kokoro

# Put the downloaded files here with these names:
# ~/kokoro/kokoro-v1.0.int8.onnx
# ~/kokoro/voices-v1.0.bin
~~~

Kokoro voice references such as `af_heart` and `bm_george` live inside that voices file; you do **not** need a separate ONNX model for each voice. The shipped catalog registers examples under `kokoro-af-heart` and `kokoro-bm-george`.

#### License implications

Engine availability is not freedom to redistribute. Before shipping or offering this publicly, check **three layers** per engine:

- **Adapter/runtime code** — `piper-tts` and `kokoro-onnx` are MIT-licensed Python packages; `onnxruntime` is MIT. These pose no restriction on use or redistribution.
- **Model weights & voices** — this is the layer that varies. Kokoro-82M weights are Apache-2.0, but the bundled voices in `voices-v1.0.bin` carry **per-voice licenses** recorded upstream; some derive from datasets that require attribution or restrict commercial use. Piper voices come from many different training datasets — each voice directory in `rhasspy/piper-voices` carries its own license, ranging from permissive/CC-BY to **non-commercial**. Check the license for each voice you deploy; it can apply to generated audio used commercially as well.
- **Bundled components** — `piper-tts` incorporates espeak-ng-derived phonemization components licensed **GPL-3.0**. Running the engine as a service is generally fine, but redistributing the engine binaries (e.g., shipping an appliance image) may trigger GPL obligations — consult counsel if that's your deployment model.

Practical rule: audit each voice/model file individually before commercial use, keep attribution where required, and don't assume "downloadable" means "redistributable."

### 3. Start the service

From the repository root (so the relative `config/` paths resolve correctly):

~~~bash
opal-talk-svc

# Equivalent:
# python -m opal_talk

# Explicit configuration, address, and port:
# opal-talk-svc --config config/service.yaml --host 127.0.0.1 --port 8001
~~~

The default address is **`http://127.0.0.1:8001`**. Keep the service terminal running. Open a second terminal, enter the repository directory, activate your virtual environment, and check the server:

~~~bash
source .venv/bin/activate

opal talk ping
opal talk info
opal talk health
opal talk engines list
opal talk voices list
~~~

Useful URLs:

| Where | Address |
| --- | --- |
| REST API | `http://127.0.0.1:8001/api/v1` |
| Interactive Swagger docs | `http://127.0.0.1:8001/docs` |
| OpenAPI contract | `http://127.0.0.1:8001/openapi.json` |
| Web console | `http://127.0.0.1:8001/ui/` **after building the SPA** |

On a fresh database, the service attempts to seed engines, voices, profiles, and settings from `config/*.yaml`. If those seed resources are invalid or model files are missing, the service may start without importing the entire seed catalog. Use `engines validate`, `voices list`, and `backup validate` to diagnose this before attempting generation.

### 4. Make it say something

Once a Piper voice is installed and `en-us-amy-low` is registered and available:

~~~bash
# Generate a finished, standard WAV file.
opal talk generate "Hello from OPAL Talk. I finally have a voice!" \
  --voice en-us-amy-low \
  -o hello.wav

# Linux with alsa-utils installed:
aplay hello.wav

# Skip the saved file and stream a WAV to the player:
opal talk generate "The future sounds pretty good." \
  --voice en-us-amy-low -o - | aplay
~~~

On Debian/Ubuntu/Raspberry Pi OS, `sudo apt install alsa-utils` installs `aplay` if needed. Generation is performed on the **server**; the CLI writes the resulting audio to the **client's** output file or stdout.

### 5. Optional: build the web console

~~~bash
cd spa
npm install
npm run build
cd ..
~~~

Restart the service if needed and open `http://127.0.0.1:8001/ui/`. The React/Vite SPA offers engine and voice management, profiles, generation and playback, settings, health, and backups—all through the REST API.

For frontend development:

~~~bash
cd spa
npm run dev
~~~

The Vite development UI uses port 5173 by default.

## Complete Typer command tree

This is the **implemented** command surface, checked against `src/opal_talk/cli/main.py` and the command modules. Everything under `opal talk` can also be invoked using `opal-talk`.

~~~text
opal
└── talk                                        (alias: opal-talk)
    │
    │  GLOBAL OPTIONS (after "talk", before the command/group)
    │  --api-url URL                            [env OPAL_TALK_API_URL]
    │  --json                                   raw JSON output
    │  --timeout SECONDS                        default: 120
    │
    ├── health                                  [--live | --ready]
    ├── info
    ├── ping
    ├── tree                                    prints this command tree
    │
    ├── engines
    │   ├── list
    │   ├── get ENGINE_ID
    │   ├── update ENGINE_ID                    [--enable/--disable]
    │   │                                       [-O KEY=VALUE]...
    │   │                                       [--options-json JSON]
    │   └── validate ENGINE_ID
    │
    ├── voices
    │   ├── list                                [--engine ENGINE_ID]
    │   │                                       [--enabled/--disabled]
    │   ├── get VOICE_ID
    │   ├── add VOICE_ID                         --name NAME --engine ENGINE_ID
    │   │                                       --ref ENGINE_VOICE_REF
    │   │                                       [--description TEXT] [--language CODE]
    │   │                                       [--enabled/--disabled]
    │   │                                       [-O KEY=VALUE]...
    │   │                                       [--options-json JSON]
    │   ├── update VOICE_ID                      [--name NAME] [--engine ENGINE_ID]
    │   │                                       [--ref ENGINE_VOICE_REF]
    │   │                                       [--description TEXT | --clear-description]
    │   │                                       [--language CODE | --clear-language]
    │   │                                       [--enabled/--disabled]
    │   │                                       [-O KEY=VALUE]...
    │   │                                       [--options-json JSON]
    │   ├── remove VOICE_ID
    │   ├── validate VOICE_ID
    │   └── test VOICE_ID                        -o OUTPUT [--text TEXT]
    │                                           [--speed FLOAT] [--volume FLOAT]
    │                                           [--format wav|pcm]
    │                                           [--sample-rate HZ]
    │                                           [-O KEY=VALUE]...
    │                                           [--options-json JSON]
    │
    ├── profiles
    │   ├── list
    │   ├── get PROFILE_ID
    │   ├── add NAME                             --voice VOICE_ID [--active]
    │   │                                       [--description TEXT]
    │   │                                       [--speed FLOAT] [--volume FLOAT]
    │   │                                       [--format wav|pcm] [--sample-rate HZ]
    │   │                                       [-O KEY=VALUE]...
    │   │                                       [--options-json JSON]
    │   ├── update PROFILE_ID                    [--name NAME] [--voice VOICE_ID]
    │   │                                       [--description TEXT | --clear-description]
    │   │                                       [--speed FLOAT] [--volume FLOAT]
    │   │                                       [--format wav|pcm] [--sample-rate HZ]
    │   │                                       [--native-rate]
    │   │                                       [--active/--inactive]
    │   │                                       [-O KEY=VALUE]...
    │   │                                       [--options-json JSON]
    │   ├── activate PROFILE_ID
    │   └── remove PROFILE_ID
    │
    ├── generate TEXT                           -o OUTPUT [--stream]
    │                                           [--profile PROFILE_ID]
    │                                           [--voice VOICE_ID] [--engine ENGINE_ID]
    │                                           [--speed FLOAT] [--volume FLOAT]
    │                                           [--format wav|pcm]
    │                                           [--sample-rate HZ]
    │                                           [-O KEY=VALUE]...
    │                                           [--options-json JSON]
    │
    ├── settings
    │   ├── list
    │   ├── get KEY
    │   ├── set KEY VALUE
    │   ├── delete KEY
    │   └── patch                               --json JSON
    │
    └── backup
        ├── list
        ├── create
        ├── download FILENAME                   [-o OUTPUT]
        ├── restore FILENAME
        ├── delete FILENAME
        ├── export                              [-o OUTPUT]
        ├── import
        └── validate                            [--payload JSON_FILE]
~~~

`opal talk tree` (or `opal-talk tree`) prints this tree live — generated by introspecting the Typer app, so it always reflects the installed version. The full Typer help is always available:

~~~bash
opal talk --help
opal talk generate --help
opal talk voices add --help
opal talk profiles update --help

# Standalone entry point:
opal-talk --help
~~~

**Global options belong immediately after `talk`:** `opal talk --json voices list` is correct; `opal talk voices list --json` is not. The root `--json` controls **output formatting**, while `settings patch --json` carries a JSON **input payload**.

## In-depth CLI guide: from first voice to real use cases

### Meet the building blocks

| Concept | Meaning | Example |
| --- | --- | --- |
| **Engine** | The TTS implementation installed on the service host | `piper`, `kokoro` |
| **Voice** | A named registration of a host-resident model/voice | `en-us-amy-low` |
| **Voice reference** | Engine-specific location or name used by an adapter | `en_US-amy-low`, `af_heart` |
| **Profile** | A reusable delivery style: voice, speed, volume, format, rate | `default`, `telephony` |
| **Generation** | Turn text into a finished or streaming audio body | `generate "Hello" -o hello.wav` |
| **Engine option** | A control relevant to a particular TTS runtime | `noise_scale`, `lang` |
| **Setting** | A stored service-wide default or limit | `chunk_max_chars` |
| **Backup** | Logical configuration archive; not the model files | `opal-talk-...tar.gz` |

The important boundary is **engine versus voice versus profile**. The engine knows *how* to synthesize; the voice tells it *which resources* to use; the profile says *how you want the result delivered*.

### Use case 1: Find out what your machine can speak

~~~bash
opal talk engines list
opal talk engines get piper
opal talk engines get kokoro

# An enabled engine isn't necessarily usable.
opal talk engines validate piper
opal talk engines validate kokoro

opal talk voices list
opal talk voices list --engine piper
opal talk voices list --engine kokoro
~~~

An engine has both `enabled` (your administrative setting) and `available` (the runtime and its required files can be used). A Piper adapter with no voice models or a Kokoro adapter with a missing ONNX file isn't ready simply because the package installed.

Check each registered voice independently:

~~~bash
opal talk voices validate en-us-amy-low
opal talk voices get en-us-amy-low
~~~

If a seed entry is missing, register a voice after installing its model (next use case).

### Use case 2: Register, test, and activate a Piper voice

The system never uploads models during registration. First put `en_US-amy-low.onnx` and `en_US-amy-low.onnx.json` on the service host, as shown in Installation, and set the engine's voices directory:

~~~bash
opal talk engines update piper -O voices_dir="$HOME/piper/voices"
opal talk engines validate piper
~~~

If the seed already created `en-us-amy-low`, **don't add the same ID again**. Instead:

~~~bash
opal talk voices get en-us-amy-low
opal talk voices validate en-us-amy-low
opal talk voices test en-us-amy-low \
  --text "Hello. My name is Amy, and I'm running locally." \
  -o amy-test.wav
aplay amy-test.wav
~~~

For an installation with no seeded voice, create it:

~~~bash
opal talk voices add en-us-amy-low \
  --name "Amy (low)" \
  --engine piper \
  --ref en_US-amy-low \
  --language en-US

opal talk voices validate en-us-amy-low
~~~

Create and activate a profile using the registered voice:

~~~bash
opal talk profiles add my-assistant \
  --voice en-us-amy-low \
  --description "Default local assistant voice" \
  --speed 1.0 --volume 0.9 --active

opal talk profiles list
opal talk generate "Welcome home." -o welcome.wav
~~~

You can also refer directly to an **absolute model path** in `--ref` instead of configuring `voices_dir`. Piper still needs the matching model config file, or a registered `config_path` option.

### Use case 3: Let Kokoro tell the same story

Once the Kokoro model and voices file are on the host:

~~~bash
opal talk engines update kokoro \
  -O model_path="$HOME/kokoro/kokoro-v1.0.int8.onnx" \
  -O voices_path="$HOME/kokoro/voices-v1.0.bin"

opal talk engines validate kokoro
opal talk voices list --engine kokoro
~~~

The seeded registration `kokoro-af-heart` is ready if it appears in `voices list` and validates. Otherwise register it yourself:

~~~bash
opal talk voices add kokoro-af-heart \
  --name "Kokoro Heart" --engine kokoro --ref af_heart \
  --language en-US
~~~

Try two distinct personalities (assuming both voices are registered and available):

~~~bash
opal talk voices test kokoro-af-heart \
  --text "Attention, explorers. Our adventure begins right now." \
  -o heart.wav

opal talk voices test kokoro-bm-george \
  --text "Attention, explorers. Our adventure begins right now." \
  -o george.wav
~~~

Run `aplay heart.wav` and `aplay george.wav` to compare them. A voice registration is **a pointer**, not a copy of the voice weights. You can change models on the host independently of the CLI's output locations.

### Use case 4: Give different jobs different voices

Profiles are reusable output recipes. You might use a friendly voice for an assistant, an authoritative voice for alerts, and a different rate for a phone system.

~~~bash
opal talk profiles list

# Make a narrator without changing the active profile:
opal talk profiles add narrator \
  --voice kokoro-bm-george \
  --description "Longer narration with a deliberate pace" \
  --speed 0.9 --volume 1.0 --format wav

# Capture the numeric profile ID from the response or from the list.
opal talk profiles list

# Use it for a specific utterance, without switching the default.
opal talk generate "Chapter one. A new beginning." \
  --voice kokoro-bm-george --speed 0.9 -o chapter-opening.wav
~~~

For an existing numeric `PROFILE_ID`:

~~~bash
opal talk profiles get PROFILE_ID
opal talk profiles activate PROFILE_ID

# Now the active profile supplies defaults:
opal talk generate "Your assistant is online." -o ready.wav

# Change this profile without changing other profiles.
opal talk profiles update PROFILE_ID --volume 0.8 --speed 1.1
~~~

Replace `PROFILE_ID` with the integer displayed by `profiles list`. A profile **must reference a registered voice**. Only one profile can be active; deleting an active profile is rejected. Switching to another profile before removal is the safe approach.

Request-level `--voice`, `--speed`, `--volume`, `--format`, and `--sample-rate` override the profile's settings for that generation. This means you can have stable defaults without giving up flexibility.

### Use case 5: Announce something immediately, without a temporary file

Generate and play a WAV in one pipeline:

~~~bash
opal talk generate \
  "The kitchen timer is finished. Dinner is ready!" \
  --voice en-us-amy-low \
  -o - | aplay
~~~

Here `-o -` means **send audio to stdout**. The CLI prints its status summary to stderr so the binary audio stream stays clean.

Want to play a particular PCM format instead of WAV? Tell **both** OPAL Talk and the player the sample rate:

~~~bash
opal talk generate "Someone is at the front door." \
  --voice en-us-amy-low \
  --format pcm --sample-rate 16000 -o - \
  | aplay -t raw -f S16_LE -c 1 -r 16000
~~~

The default sample rate is generally the voice's **native** rate. For arbitrary raw PCM pipelines, don't guess—set `--sample-rate` explicitly.

### Use case 6: Speak while the sentence is still being generated

For announcements, voice assistants, and long responses, waiting for a complete file makes the system feel sluggish. Use **`--stream`**.

~~~bash
opal talk generate \
  "Attention please. The camera saw movement. Checking the front door now." \
  --voice en-us-amy-low \
  --stream --sample-rate 22050 -o - \
  | aplay -q -t raw -f S16_LE -c 1 -r 22050
~~~

Unlike a regular WAV request, a stream is **always signed 16-bit little-endian mono raw PCM**. `--format` cannot be combined with `--stream`.

You can pipe a file into generation without loading all of it as a shell argument:

~~~bash
opal talk generate - \
  --voice en-us-amy-low \
  --stream --sample-rate 22050 -o - \
  < my-announcement.txt \
  | aplay -q -t raw -f S16_LE -c 1 -r 22050
~~~

To influence how quickly the first chunk becomes available:

~~~bash
# Smaller chunks often improve first-audio latency at the cost of more overhead.
opal talk settings get chunk_max_chars
opal talk settings set chunk_max_chars 200
~~~

Text is chunked internally at sentence boundaries. It is **not** one independent audio file per sentence, and smaller chunks are not a guarantee of a particular latency. Model load, hardware, and engine affect first audio too.

> **Streaming failure behavior:** An error before audio starts is a normal API error. If a model fails *after* bytes have begun streaming, the response may terminate incomplete; the CLI reports a failure and the bytes already written are not a complete utterance.

### Use case 7: Turn a long text file into spoken narration

~~~bash
# Complete WAV, read from standard input.
opal talk generate - --voice kokoro-af-heart -o article.wav < article.txt

# Or stream a long article to your speakers.
opal talk generate - --voice kokoro-af-heart \
  --stream --sample-rate 24000 -o - \
  < article.txt \
  | aplay -q -t raw -f S16_LE -c 1 -r 24000
~~~

The configured `max_text_chars` limits accepted text (default: 20,000). You can inspect and adjust it deliberately:

~~~bash
opal talk settings list
opal talk settings set max_text_chars 50000
~~~

The limit guards the service from unexpectedly large synthesis requests. For a full book, consider dividing it into chapters with one output file per chapter rather than one giant generation call.

### Use case 8: Make an 8 kHz voice prompt for a telephone system

OPAL Talk can output at a chosen sample rate instead of leaving conversion to every downstream program.

~~~bash
opal talk profiles list

# The fresh seed includes a profile named "telephony"; use its numeric ID.
# It normally supplies raw PCM at 8000 Hz.
opal talk generate "Your call is important to us." \
  --voice en-us-amy-low --format pcm --sample-rate 8000 \
  -o phone.raw
~~~

Convert a raw 8 kHz prompt to WAV or μ-law with SoX if you have it installed:

~~~bash
sox -t raw -r 8000 -e signed-integer -b 16 -c 1 \
  phone.raw phone.wav

sox -t raw -r 8000 -e signed-integer -b 16 -c 1 \
  phone.raw -e mu-law phone-ulaw.wav
~~~

Raw `pcm` has **no WAV header**. If your target wants an ordinary WAV, request it directly:

~~~bash
opal talk generate "Please hold." \
  --voice en-us-amy-low --format wav --sample-rate 8000 \
  -o hold.wav
~~~

This is useful for IVR prompts, embedded systems, constrained playback devices, or audio-pipeline tests.

### Use case 9: Fine-tune the voice without changing code

Some controls are **portable** (every engine understands them):

~~~bash
opal talk generate "This is a quiet announcement." \
  --voice en-us-amy-low --speed 0.85 --volume 0.65 \
  -o quiet.wav
~~~

Others are specific to an engine and are passed with repeatable `-O KEY=VALUE` or `--options-json`:

~~~bash
# Piper: adjust expressiveness and rhythm.
opal talk generate "The workshop is ready." \
  --voice en-us-amy-low \
  -O noise_scale=0.35 -O noise_w_scale=0.6 \
  -o piper-tuned.wav

# Kokoro: choose a pronunciation language for this request.
opal talk generate "Good evening." \
  --voice kokoro-bm-george \
  -O lang=en-gb \
  -o british.wav

# JSON form for several options at once:
opal talk engines update piper \
  --options-json '{"voices_dir":"~/piper/voices","cache_size":4}'
~~~

Engine options are *scoped*:

| Where you set it | Applies to | Example |
| --- | --- | --- |
| `engines update` | Runtime-wide options | Piper `voices_dir`, `use_cuda`, `cache_size` |
| `voices add/update` | Registered voice's options/defaults | Piper `config_path` |
| `profiles add/update` | A reusable synthesis profile | `noise_scale`, Kokoro `lang` |
| `generate` / `voices test` | One request only | Temporary expressiveness change |

For synthesis, engine options layer as **voice → profile → request**, with more specific values winning. An engine override is advanced: the target engine must actually support the selected voice.

**Mind the limits:** OPAL Talk validates options rather than guessing. Piper supports speed 0.25–4.0, while Kokoro supports 0.5–2.0; unsupported values cause a clear error.

### Use case 10: Run a voice comparison

Want to compare delivery without changing your default profile?

~~~bash
mkdir -p comparisons

opal talk voices test en-us-amy-low \
  --text "The sky is clear and the journey begins." \
  --speed 1.0 -o comparisons/amy.wav

opal talk voices test kokoro-af-heart \
  --text "The sky is clear and the journey begins." \
  --speed 1.0 -o comparisons/heart.wav

opal talk voices test kokoro-bm-george \
  --text "The sky is clear and the journey begins." \
  --speed 1.0 -o comparisons/george.wav
~~~

For `voices test`, the specified voice is used **without active-profile fallbacks**. That's ideal for A/B listening. Use `voices validate VOICE_ID` to diagnose failures before comparing.

A simple batch of different text lines (one file per line):

~~~bash
mkdir -p batch
n=0
while IFS= read -r line; do
  n=$((n + 1))
  opal talk generate "$line" --voice en-us-amy-low \
    -o "$(printf 'batch/line-%03d.wav' "$n")"
done < announcements.txt
~~~

The batch uses the REST API for every line; it is not an offline bypass of the service.

### Use case 11: Give OPAL a voice when something happens

OPAL Listen detects commands. OPAL Look detects things. OPAL Event can match the resulting events and invoke actions. OPAL Talk turns an action's text response into audio.

~~~text
    OPAL Look                    OPAL Listen
 "person at the door"          "what time is it?"
         │                             │
         └───────────┬─────────────────┘
                     ▼
                 OPAL Event
                match + route
                     │
             trusted action/script
                     │
                     ▼
          OPAL Talk REST / Typer CLI
                     │
                     ▼
                 WAV or PCM
                     │
             speaker/audio sink
~~~

For example, this **shell-side** announcement is the final talking step a trusted automation could call:

~~~bash
opal-talk generate "A person has been detected at the front door." \
  --voice en-us-amy-low \
  --stream --sample-rate 16000 -o - \
  | aplay -q -t raw -f S16_LE -c 1 -r 16000
~~~

OPAL Event does **not** automatically call this command just because the repositories are installed. You must define an appropriate Event action and rule. For dynamic event data, supply text as **argv or stdin data**, not by interpolating untrusted input into executable shell source.

Also useful: combining with OPAL Listen to answer the `get_time` intent, or with OPAL Think to speak generated responses. Those are integration patterns built from the REST interface; they aren't silently wired together by default.

### Use case 12: Send speech from another program

The CLI is a convenient REST client, but you can integrate without invoking a subprocess.

~~~bash
# Completed WAV through the REST API:
curl -fS \
  -X POST http://127.0.0.1:8001/api/v1/generate \
  -H 'Content-Type: application/json' \
  -d '{"text":"Hello from an external application.","voice_id":"en-us-amy-low","output_format":"wav"}' \
  -o api-hello.wav
~~~

Or from Python:

~~~python
import httpx

with httpx.Client(base_url="http://127.0.0.1:8001", timeout=120) as client:
    response = client.post(
        "/api/v1/generate",
        json={
            "text": "The automation is complete.",
            "voice_id": "en-us-amy-low",
            "output_format": "wav",
        },
    )
    response.raise_for_status()
    with open("automation-complete.wav", "wb") as audio_file:
        audio_file.write(response.content)
~~~

The service returns audio metadata in headers including `X-Opal-Audio-Format`, `X-Opal-Sample-Rate`, `X-Opal-Channels`, `X-Opal-Engine-Id`, and `X-Opal-Voice-Id`. Use those values when you write a player or converter rather than assuming every engine has the same native rate.

### Use case 13: Back up your favorite voices and profiles

A working voice catalog is worth keeping. Take a snapshot before importing configuration or experimenting with engine settings:

~~~bash
opal talk backup validate
opal talk backup create
opal talk backup list

# Save a human-readable logical config export locally:
opal talk backup export -o talk-config.json

# Check a saved export/backup payload:
opal talk backup validate --payload talk-config.json
~~~

Backups are created **on the service host** and can be downloaded by the filename returned from `backup list`:

~~~bash
opal talk backup download FILENAME -o ./my-talk-backup.tar.gz
~~~

For an existing server backup filename:

~~~bash
# This replaces the service configuration immediately.
opal talk backup restore FILENAME
~~~

A backup captures logical configuration, **not** Piper/Kokoro model binaries. To migrate to another computer, you must install the needed voice/model files there too.

Want your YAML files to be the source of truth?

~~~bash
# On the server, edit config/engines.yaml, voices.yaml,
# profiles.yaml, or settings.yaml, then validate and back up.
opal talk backup validate
opal talk backup create

# Re-import server-side YAML:
opal talk backup import
opal talk backup validate
~~~

**Caution:** `backup import` reads the **server's** configured YAML directory; it does **not** upload a JSON export from the client's filesystem. It applies present sections as replacements and doesn't automatically create a safety backup first. Back up explicitly before importing. Restore also changes the live configuration without a confirmation prompt.

### Use case 14: Know what broke before users hear silence

~~~bash
opal talk ping
opal talk info
opal talk health
opal talk health --live
opal talk health --ready

opal talk engines list
opal talk engines validate piper
opal talk voices validate en-us-amy-low
opal talk backup validate
~~~

If your pipeline can't speak, troubleshoot from the bottom up:

| Symptom | First place to look |
| --- | --- |
| Can't reach the service | `opal talk ping`, URL, port, process |
| Engine reports unavailable | `engines validate`, installed Python extra, model paths |
| Voice doesn't synthesize | `voices validate`, voice ref and model config |
| No active default voice | `profiles list`, active profile or explicit `--voice` |
| Voice works but a pipe sounds like noise | Raw PCM sample rate, sample format, channels |
| Stream starts then cuts off | Engine logs and incomplete-read error |
| Changes to config YAML seem ignored | `backup import` or persisted engine/settings state |
| Restore broke generation | Model files may be missing from the new host |

Remember: **the first request may load a model**. A quick warmup can help measure steady-state performance:

~~~bash
opal talk voices test en-us-amy-low --text "Ready." -o /dev/null
~~~

## Settings and reference material

### A quick settings reference

Service-wide defaults can be set individually:

~~~bash
opal talk settings list
opal talk settings get chunk_max_chars
opal talk settings set chunk_max_chars 200
opal talk settings set default_speed 1.1

# Remove a stored override; fall back to the built-in behavior.
opal talk settings delete default_speed

# Atomic multi-key merge patch, when desired:
opal talk settings patch --json '{"default_volume":0.85,"chunk_max_chars":250}'
~~~

| Key | Built-in default | Purpose |
| --- | --- | --- |
| `max_text_chars` | 20,000 | Reject text above the configured maximum |
| `max_audio_bytes` | 128 MiB | Cap on the encoded non-streaming response body (WAV container included) |
| `chunk_max_chars` | 400 | Size limit for internal sentence chunks |
| `default_speed` | 1.0 | Service-wide fallback speed |
| `default_volume` | 1.0 | Service-wide fallback gain |
| `default_output_format` | `wav` | Service-wide fallback output |

`settings list` returns **stored** settings, not every built-in default. A missing `settings get KEY` result doesn't mean the default doesn't exist. Streaming responses are governed by text and runtime resource limits, not `max_audio_bytes`.

For a generation request, the precedence is: **explicit request → selected/active profile → service setting → built-in default**. The voice usually determines the engine and its native sample rate.

### Audio output cheatsheet

| Output | Content | How to use |
| --- | --- | --- |
| WAV | Finished RIFF/WAV | Play directly or save as a standard audio file |
| PCM | Finished raw S16LE mono | Specify sample rate to the player |
| `--stream` | Progressive raw S16LE mono | Stream bytes directly to a player or application |

For low-latency pipelines, set `--sample-rate` to a known value, especially when comparing Piper and Kokoro. The service handles resampling; the receiving player must use the matching rate.

### Configuration and API philosophy

The implemented API is **v1.4.0**. The authoritative copy is the packaged document `src/opal_talk/contract/opal_talk.yaml`: it is loaded by `opal_talk.contract.load_contract()`, drives the server's `/openapi.json` endpoint, and validates every test-suite response. The root [`opal_talk_v1.4.0.yaml`](opal_talk_v1.4.0.yaml) is a byte-identical human-facing copy; a test enforces that the two never drift. Interpretations of under-specified details are recorded in [docs/CONTRACT_NOTES.md](docs/CONTRACT_NOTES.md).

The full service is **API-first**:

~~~text
   Typer CLI ─────────────────┐
   React SPA ─────────────────┼────▶ FastAPI REST contract
   Other clients ─────────────┘                │
                                             ▼
                                      Service layer
                                  generation / catalogs
                                             │
                 ┌───────────────────────────┴──────────────────┐
                 ▼                                              ▼
         Persistence / config                           Engine-neutral TTS
              SQLite                                chunk / resample / encode
                                                               │
                                                    ┌──────────┴─────────┐
                                                    ▼                    ▼
                                                  Piper                Kokoro
~~~

The REST service coordinates profiles, registered voices, generation, validation, persistence, configuration imports, and streaming. The CLI and SPA **do not** duplicate engine logic. Custom engines integrate through the Python `TtsEngine` interface and the `opal_talk.engines` entry-point group.

Process configuration lives in [`config/service.yaml`](config/service.yaml). The main environment overrides are `OPAL_TALK_CONFIG`, `OPAL_TALK_HOST`, `OPAL_TALK_PORT`, `OPAL_TALK_DATA_DIR`, `OPAL_TALK_CONFIG_DIR`, and `OPAL_TALK_SPA_DIR`. The CLI's target is controlled separately by `OPAL_TALK_API_URL` (default `http://127.0.0.1:8001`).

For security, keep the default loopback binding unless you intend to operate a network service. If you expose the API to other hosts, place it behind appropriate network access controls or an authenticated reverse proxy. Voice synthesis consumes CPU, memory, and potentially GPU resources; avoid unrestricted public access.

## Test, develop, and extend

From the repository root:

~~~bash
make test           # Python tests: API, CLI, streaming, contract, engines
make test-fast      # Python tests without real-engine requirements
make lint           # Ruff and related checks
make test-spa       # React/Vitest and frontend type checking
make spa-dev        # Vite development server
~~~

Real-engine tests require installed model files — they look for `~/piper/voices/en_US-amy-low.onnx` and `~/kokoro/` (override with `OPAL_TALK_TEST_PIPER_MODEL` / `OPAL_TALK_TEST_KOKORO_DIR`) — and skip cleanly when the resources aren't available. Standard CI runs `make test-fast`; real-engine validation is a manual-dispatch job only, so no model downloads ever happen on push or PR. The test suite checks OpenAPI route alignment, CLI/SPA operation parity, output formats, error handling, and streaming behavior. See [docs/PERFORMANCE_TESTS.md](docs/PERFORMANCE_TESTS.md) for CPU/GPU/Raspberry Pi notes and [docs/RELEASE_VALIDATION.md](docs/RELEASE_VALIDATION.md) for the real-engine release checklist.

### Find your way around

~~~text
opal-talk/
├── opal-talk.png                       project artwork
├── README.md                           this guide
├── opal_talk_v1.4.0.yaml               authoritative OpenAPI contract (v1.4.0)
├── pyproject.toml                      packaging and entry points
├── Makefile                            development commands
├── config/
│   ├── service.yaml                    host, port, paths, CORS
│   ├── engines.yaml                    engine settings
│   ├── voices.yaml                     voice registrations
│   ├── profiles.yaml                   generation profiles
│   └── settings.yaml                   service defaults
├── src/opal_talk/
│   ├── api/                            FastAPI routes and middleware
│   ├── cli/                            Typer client and commands
│   ├── service/                        generation, config, backup
│   ├── tts/                            Piper, Kokoro, encoding, chunking
│   ├── persistence/                    SQLite catalog and state
│   └── contract/                       packaged OpenAPI specification
├── spa/                                React + Vite web console
├── docs/
│   ├── CLI.md                          exhaustive command reference
│   ├── USER_GUIDE.md                   detailed operating manual
│   ├── CONTRACT_NOTES.md               contract interpretation notes
│   ├── PERFORMANCE_TESTS.md            performance testing notes
│   └── RELEASE_VALIDATION.md           real-engine release checklist
└── tests/                              Python and contract tests
~~~

For the deeper material, see the [CLI Reference](docs/CLI.md), [User Guide](docs/USER_GUIDE.md), [Contract Notes](docs/CONTRACT_NOTES.md), [Performance Tests](docs/PERFORMANCE_TESTS.md), and [Release Validation](docs/RELEASE_VALIDATION.md).

---

**OPAL Talk doesn't decide what to say. It makes the words audible.** Put a local speech engine behind a simple REST contract, give it the voice that fits the moment, and let the rest of OPAL decide when it's time to speak.

[Explore OPAL](https://github.com/PatMartin/opal) · [OPAL Listen](https://github.com/PatMartin/opal-listen) · [OPAL Event](https://github.com/PatMartin/opal-event) · [OPAL Look](https://github.com/PatMartin/opal-look) · [OPAL Think](https://github.com/PatMartin/opal-think)
