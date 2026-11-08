# OPAL Say Cookbook

This cookbook is a collection of small, practical recipes rather than a replacement for
the [CLI reference](CLI.md). Every command block is fenced as `bash`, so documentation
renderers that provide a copy control show a **copy button** for the complete snippet.

## Before you start

Start OPAL Say, install an engine and at least one voice model, register that model as a
voice, and make sure its health check succeeds. The examples use the seeded
`en-us-amy-low` Piper voice and `kokoro-af-heart` Kokoro voice; substitute IDs from
`opal say voice list` when your installation differs. If `voice list` prints `(none)`,
generation cannot work yet: follow [PIPER_INSTALL.md](PIPER_INSTALL.md) or
[KOKORO_INSTALL.md](KOKORO_INSTALL.md), then register and validate a voice before using
the recipes.

```bash
opal-say-svc

# Run these in another shell after activating the project's virtual environment.
opal say health
opal say voice list
opal say voice validate en-us-amy-low
```

The service writes audio; it does not play it. Recipes that play audio either use `opal say generate --play`
for local playback through the optional `simpleaudio` extra (`pip install 'opal-say[audio]'`), with an
automatic stdlib `winsound` fallback on Windows — or pipe to Linux `aplay`, which can be adapted to
another local player. `-o -` sends audio to standard output, while status summaries are sent to
standard error.

---

# 20 things OPAL Say can do on its own

## 1. Make a local spoken desktop announcement

Create a normal WAV file with an explicit registered voice, then use any WAV-capable local
player. This does not depend on an active profile being configured.

```bash
opal say generate "Build complete. All checks passed." \
  --voice en-us-amy-low -o build-complete.wav
aplay build-complete.wav
```

## 2. Speak a message immediately without leaving an audio file behind

Plain text after `say` generates and plays locally through `simpleaudio` — no file, no pipe.
This works well for one-off reminders.

```bash
opal say generate --play "Tea timer finished." --voice en-us-amy-low
# Equivalent explicit form:
# opal say generate "Tea timer finished." --voice en-us-amy-low --play
```

## 3. Start playback before a long announcement has finished synthesizing

Progressive generation returns raw S16LE mono PCM as it becomes available. Force the
sample rate so the player's configuration is deterministic.

```bash
opal say generate \
  "The nightly maintenance window begins in five minutes. Please save your work." \
  --voice en-us-amy-low --stream --sample-rate 16000 -o - \
  | aplay -q -t raw -f S16_LE -c 1 -r 16000
```

## 4. Convert a text file into a narrated chapter

Pass `-` as the text argument to read standard input. OPAL Say handles sentence chunking
inside the service, so a chapter need not be split by the shell.

```bash
opal say generate - --voice en-us-amy-low -o chapter-01.wav < chapter-01.txt
```

## 5. Produce an 8 kHz raw prompt for a telephony pipeline

Raw PCM is useful when the downstream system supplies its own container or protocol.
The explicit sample rate keeps the file compatible with narrowband phone systems.

```bash
opal say generate "Your call is important to us." \
  --voice en-us-amy-low --format pcm --sample-rate 8000 \
  -o hold-message.s16le
```

## 6. Produce the same prompt as a portable WAV file

Use output resampling to create an asset with a known rate for another application. The
result remains a standard mono signed-16-bit WAV.

```bash
opal say generate "Welcome to the museum audio guide." \
  --voice kokoro-af-heart --sample-rate 24000 \
  -o welcome.wav
```

## 7. Create a distinct “urgent” delivery without changing defaults

Request-level speed and volume take precedence over the profile and service defaults.
That makes emergency announcements explicit and isolated.

```bash
opal say generate "Warning. The garage door is open." \
  --voice en-us-amy-low --speed 1.2 --volume 1.35 \
  -o garage-warning.wav
```

## 8. Use a different language setting for one Kokoro request

Engine options are scoped to the request and do not alter the registered voice or active
profile. This is useful for multilingual scripts.

```bash
opal say generate "Bonjour et bienvenue." \
  --voice kokoro-af-heart -O lang=fr-fr \
  -o welcome-fr.wav
```

## 9. Build an A/B voice audition pack

`voice test` bypasses active-profile selection, which makes it reliable for comparing
registered voices against the same phrase.

```bash
mkdir -p auditions
opal say voice test en-us-amy-low --text "The sky is clear and the journey begins." -o auditions/amy.wav
opal say voice test kokoro-af-heart --text "The sky is clear and the journey begins." -o auditions/heart.wav

# Or listen without writing files at all:
opal say voice test en-us-amy-low --text "The sky is clear and the journey begins." --play
opal say voice test kokoro-af-heart --text "The sky is clear and the journey begins." --play
```

## 10. Define a focused profile for announcements

Profiles package a voice and delivery settings under a numerical ID. Create one, note the
ID printed by the command, then select it explicitly in future generation.

```bash
opal say profile add announcements \
  --voice en-us-amy-low --speed 1.1 --volume 1.2 --sample-rate 16000
opal say profile list
```

## 11. Switch the default voice context for an entire deployment

Activate a profile once to make it the fallback for calls that do not specify a profile or
voice. Replace `2` with the ID shown by `profile list`.

```bash
opal say profile activate 2
opal say generate "This message uses the newly active profile." -o active-profile.wav
```

## 12. Run a batch voice-over job from a list of lines

Each input line becomes an individual WAV asset. The `printf` sequence gives stable,
sortable filenames for a presentation or kiosk.

```bash
mkdir -p announcements
n=0
while IFS= read -r line; do
  n=$((n + 1))
  opal say generate "$line" --voice en-us-amy-low \
    -o "$(printf 'announcements/item-%03d.wav' "$n")"
done < announcements.txt
```

## 13. Let a script route messages to a remote Say service

The CLI is a REST client, so setting `OPAL_SAY_API_URL` moves synthesis to a service host
without changing the command pattern. The audio file is written on the client that runs
the CLI.

```bash
export OPAL_SAY_API_URL=http://speech-host:8001
opal say generate "The greenhouse temperature is stable." \
  --voice en-us-amy-low -o greenhouse-status.wav
```

## 14. Register a new model already present on the service host

Voice registration stores a stable name for an existing model; it does not upload a model.
Validate immediately so a broken model path is discovered before use.

```bash
opal say voice add en-us-ryan-medium \
  --name "Ryan (medium)" --engine piper --ref en_US-ryan-medium --language en-US
opal say voice validate en-us-ryan-medium
```

## 15. Disable an engine during maintenance

Disabling routing prevents new requests from selecting an engine while preserving its
configuration. Validate and re-enable it when maintenance is complete.

```bash
opal say engine update kokoro --disable
opal say engine update kokoro --enable
opal say engine validate kokoro
```

## 16. Tune the Piper model cache for a voice-heavy installation

Engine options are engine-specific and merged with existing options. This raises Piper's
model cache size without overwriting the configured voice directory.

```bash
opal say engine update piper -O cache_size=8
opal say engine get piper
```

## 17. Reduce time-to-first-audio for streamed notices

Smaller chunks can start the first stream sooner. This is a service-wide trade-off, so
inspect the existing setting and choose a value appropriate to your hardware.

```bash
opal say setting get chunk_max_chars
opal say setting set chunk_max_chars 200
```

## 18. Give a large deployment a consistent default speaking pace

Service settings are fallbacks only: an explicit profile or generation option still wins.
This is a good way to adjust behavior of simple callers without editing each caller.

```bash
opal say setting set default_speed 1.08
opal say setting set default_volume 0.9
```

## 19. Export configuration for review or version control

Exports contain voice registrations, profiles, engine options, and settings—not model
weights or generated audio. Review the resulting JSON before sharing it.

```bash
opal say backup export -o say-configuration.json
opal say backup validate --payload say-configuration.json
```

## 20. Take a recoverable configuration snapshot before an upgrade

Create the server-side archive, list it to capture its generated filename, then download a
copy to the current machine. Restore requires the backup to remain on the service host.

```bash
opal say backup create
opal say backup list
# Copy the filename shown above into the next command.
opal say backup download opal-say-2026-10-12T162205Z.tar.gz -o before-upgrade.tar.gz
```

---

# 20 things the OPAL modules can do together

These recipes assume the wider OPAL ecosystem is installed: **OPAL Look** observes,
**OPAL Listen** recognizes spoken commands, **OPAL Think** supplies reasoning or generated text,
**OPAL Event** matches trusted events and runs actions, and **OPAL Say** makes a response audible.

The exact command tree and rule syntax for the other modules belong to their own packages. Each recipe
therefore shows the safe, final Say action that an OPAL Event rule can run after it has received
structured data from Look, Listen, or Think. For dynamic text, pass data as an argument or standard
input; never interpolate untrusted event values into shell source.

## 21. Announce a visitor detected by OPAL Look

Let Look emit the detection, Event apply a trusted rule, and Say play the resulting fixed safety
announcement.

```bash
opal-say generate --play "A person has been detected at the front door." \
  --voice en-us-amy-low --stream --sample-rate 16000
```

## 22. Respond to an OPAL Listen “what time is it?” intent

Have Event or Think construct the trusted response text, then provide it on standard input. This keeps
the shell code constant even when the spoken value changes.

```bash
printf 'It is %s.\n' "$(date '+%H:%M')" \
  | opal-say generate --play - --voice en-us-amy-low
```

## 23. Speak a concise answer generated by OPAL Think

Think can produce text, Event can decide it is appropriate to announce, and the final action reads
the answer safely from a file or pipe.

```bash
cat think-answer.txt \
  | opal-say generate --play - --profile 1
```

## 24. Turn a recognized voice command into a confirmation

After Listen identifies a known intent and Event authorizes it, provide a fixed confirmation. Keeping
confirmation wording fixed avoids speaking arbitrary recognition input back to the room.

```bash
opal-say generate --play "The kitchen lights have been turned on." \
  --voice en-us-amy-low --speed 1.1
```

## 25. Make a camera privacy warning audible

Let Look detect entry into a protected area, Event enforce the rule, Think optionally choose the
locale, and Say deliver a prominent warning.

```bash
opal-say generate "This area is monitored. Please do not enter." \
  --voice kokoro-af-heart --volume 1.3 --sample-rate 24000 -o privacy-warning.wav
aplay privacy-warning.wav
```

## 26. Give spoken feedback when a package is delivered

Event can correlate a Look detection with a delivery workflow and invoke a stable home announcement.

```bash
opal-say generate --play "A delivery has arrived at the front door." \
  --voice en-us-amy-low
```

## 27. Read a Think-generated daily briefing as it is produced

Use a progressive stream for a briefing assembled from calendar, weather, and automation events.
The producer writes the final briefing to standard input.

```bash
cat daily-briefing.txt \
  | opal-say generate --play - --voice kokoro-af-heart --stream --sample-rate 24000
```

## 28. Escalate an unattended smoke or leak alert

Look or another sensor publishes the condition, Event decides it is urgent, and Say uses a dedicated
high-volume profile. Substitute the ID for your actual emergency profile.

```bash
opal-say generate --play "Emergency. Water has been detected in the utility room." \
  --profile 4 --volume 1.5
```

## 29. Confirm a recognized access request

Listen recognizes a predefined request, Think can verify context, Event applies authorization, and Say
responds without disclosing sensitive details.

```bash
opal-say generate --play "Your request has been accepted." \
  --voice en-us-amy-low
```

## 30. Narrate detected objects for an accessibility workflow

Have Look produce a curated object label and make Event pass it as data through standard input.
The text is not evaluated by the shell.

```bash
printf '%s\n' "A chair is two metres ahead." \
  | opal-say generate --play - --voice en-us-amy-low --speed 0.9
```

## 31. Speak a Think-assisted “what do I see?” answer

Look supplies detections, Think summarizes only useful observations, Listen starts the interaction,
and Event invokes the Say action with the summary.

```bash
cat scene-summary.txt \
  | opal-say generate - --voice kokoro-af-heart -O lang=en-us -o scene-summary.wav
aplay scene-summary.wav
```

## 32. Broadcast a contextual home-arrival greeting

Look recognizes arrival, Event checks the time of day, Think supplies the contextual wording, and Say
renders the resulting message with a friendly profile.

```bash
printf '%s\n' "Welcome home. The living room is 21 degrees." \
  | opal-say generate --play - --profile 2
```

## 33. Give an audible result after a voice-controlled task

Listen extracts the intent, Event performs the trusted task, Think summarizes the result, and Say
announces only the summary received over standard input.

```bash
cat task-result.txt \
  | opal-say generate - --voice en-us-amy-low --sample-rate 16000 -o task-result.wav
aplay task-result.wav
```

## 34. Announce a camera-based perimeter event in a specific language

Use Event routing to select the locale, and apply the matching engine option only to this request.

```bash
opal-say generate "Attention. Movement has been detected near the gate." \
  --voice kokoro-af-heart -O lang=en-gb -o perimeter-alert.wav
aplay perimeter-alert.wav
```

## 35. Turn a voice query into an intercom-style response

Listen recognizes the request, Think drafts a short answer, Event routes it to the room speaker, and
Say streams it with a fixed raw format expected by the speaker process.

```bash
cat intercom-answer.txt \
  | opal-say generate --play - --voice en-us-amy-low --stream --sample-rate 16000
```

## 36. Deliver an intelligent, event-driven maintenance reminder

Event can combine device telemetry with a Think-generated explanation. Save an audio artifact too, so
the same message can be replayed on a dashboard.

```bash
cat maintenance-reminder.txt \
  | opal-say generate - --voice en-us-amy-low -o maintenance-reminder.wav
aplay maintenance-reminder.wav
```

## 37. Announce only the safe summary of an external event

Let Event validate and normalize external event data, Think summarize it, and pass the plain final
sentence to Say. Do not construct the command by concatenating event data.

```bash
cat safe-event-summary.txt \
  | opal-say generate --play - --voice en-us-amy-low --volume 1.1
```

## 38. Provide a spoken status after an OPAL health workflow

Event can collect component health, Think can phrase a human-readable status, and Say makes it
available even when no display is nearby.

```bash
cat opal-health-summary.txt \
  | opal-say generate - --voice kokoro-af-heart -o opal-health-summary.wav
aplay opal-health-summary.wav
```

## 39. Create a multi-room announcement asset once

After Event chooses the audience and Think generates a neutral message, generate one portable WAV for
distribution to separate room players or dashboards.

```bash
cat whole-home-announcement.txt \
  | opal-say generate - --voice en-us-amy-low --sample-rate 16000 \
    -o whole-home-announcement.wav
```

## 40. Make the whole stack recover gracefully after configuration changes

Before an Event-driven deployment change, snapshot Say's routing configuration; after the change,
validate that the voices and profiles supporting Listen, Look, and Think responses remain usable.

```bash
opal-say backup create
opal-say backup validate
opal-say health --ready
```

## Operational notes

- Use `opal-say` rather than `opal say` in automation scripts when multiple OPAL packages share a
  Python environment; each package can install the shared `opal` command.
- Treat generated wording as data. Use `opal-say generate --play -` (or `opal-say generate - -o file.wav` for file output) with
  a pipe or redirected file for dynamic text rather than generating shell code from recognition,
  vision, or language-model output.
- Use a profile for persistent contexts (telephony, emergency alerts, intercom) and request options
  for one-off changes.
- Before restoring or importing configuration, create a backup. A restore replaces the complete
  configuration and does not create a safety backup automatically.
