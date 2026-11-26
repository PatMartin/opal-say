# `opal say` CLI reference

Contract **0.5.0**, API prefix **`/api/v1`**. The CLI is a REST client, not a second database or synthesis implementation. Start `opal-say-svc` first. `opal-say ...` is the standalone equivalent of `opal say ...` and avoids entry-point conflicts with sibling OPAL installations.

## Connection, credentials and output

```bash
opal say --help
opal say tree --ascii
opal say --api-url http://127.0.0.1:8001 --json service health
```

Global options precede the group: `--api-url` (also `OPAL_SAY_API_URL`), `--timeout`, `--json`, `--env-file`, and `--no-env-file`. `OPAL_SAY_TOKEN` supplies the bearer credential when authentication is enabled. Keep credentials in environment variables, not command arguments, profile files or snapshots.

Configuration writers fetch a strong whole-configuration ETag and send `If-Match`. HTTP failures exit 1; connection or argument failures exit 2. Invalid validation reports exit 1. Audio goes to the selected output; diagnostics go to stderr.

`service health` is the human status command. Machine callers use `GET /api/v1/health/live` and `GET /api/v1/health/ready` directly; these probes deliberately have no CLI binding.

## Voices

```bash
opal say voice list --engine piper --enabled
opal say voice list --language en-US
opal say voice add amy --name Amy --engine piper --ref en_US-amy-low --language en-US
opal say voice get amy
opal say voice update amy --description "Local assistant"
opal say voice validate amy
opal say voice test amy --text "Ready." -o test.wav
opal say voice remove unused-voice
```

`add` and `update` accept `--name`, `--engine`, `--ref`, `--description`, `--language`, `--enabled/--disabled`, repeated `-O KEY=VALUE`, and `--options-json JSON` where applicable. `update` also has `--clear-description` and `--clear-language`. Voice options replace that voice's complete option map. Model files must already exist on the server; registration never uploads or deletes them. An in-use voice cannot be removed, and the active default voice cannot be disabled.

## Namespaced profiles

Profile IDs are **opaque strings**, not integers. OPAL Say owns only `modules.opal-say`. Shared and foreign module compartments are portable data and must not be edited through this module.

Create `profile.json`:

```json
{
  "name": "assistant",
  "is_active": true,
  "shared": {},
  "modules": {
    "opal-say": {
      "voice_id": "amy",
      "speed": 1.0,
      "volume": 0.9,
      "output_format": "wav",
      "sample_rate": null,
      "engine_options": {}
    }
  }
}
```

```bash
opal say profile add --in profile.json
opal say profile list
opal say profile show PROFILE_ID
opal say profile activate PROFILE_ID
opal say profile remove INACTIVE_PROFILE_ID
```

Alternatively use `profile add --name NAME [--description TEXT] [--is-active/--no-is-active] [--shared FILE] [--module FILE]`. `--shared` is a JSON/YAML object; `--module` is just the Say defaults object, without the `modules` wrapper. `--in` supplies the whole document and cannot be mixed with field options.

`profile update PROFILE_ID` accepts the same input styles. Its `--in` file is a profile patch, not an entire exported read record. Own-compartment fields merge; null removes an override, except `sample_rate: null`, which explicitly lets the service choose the rate. An empty `engine_options` object clears that map. `--no-is-active` does not deselect the active profile. Activate another profile before deleting the current one.

```json
{"volume": 0.8, "speed": 1.1}
```

Save that object as `delivery.json`, then run:

```bash
opal say profile update PROFILE_ID --module delivery.json
```

## Speech

```bash
opal say generate "Hello." --voice amy -o hello.wav
opal say generate "As Amy." --persona-id amy-persona --play
opal say generate "Profile delivery." --profile PROFILE_ID -o profile.wav
opal say generate - --voice amy -o article.wav < article.txt
opal say generate "Streaming." --voice amy --stream --sample-rate 16000 -o stream.pcm
opal say generate "Incremental." --voice amy --incremental -o incremental.pcm
```

Generation supports `--profile`, `--voice`, canonical `--persona-id`, retained alias `--persona`, `--engine`, `--speed`, `--volume`, `--format wav|pcm`, `--sample-rate`, repeated `-O KEY=VALUE`, and `--options-json JSON`. Different values for the two persona selectors are rejected. Explicit request values win over persona voice selection, namespaced profile defaults, and service defaults. Personas and engine options are managed through configuration snapshots (`resources.personas`, `resources.engines`) or a configured persona owner, not dedicated CLI commands.

Choose `-o FILE`, `-o -` (binary stdout), or `--play`. Playback requires the optional `audio` extra and occurs on the CLI host, never the server. Supplying plain text directly to `opal say` is shorthand for local playback.

Both streaming modes return raw mono signed 16-bit little-endian PCM; `--format` is not allowed with either. Match a raw PCM player's sample rate to the metadata or set `--sample-rate` explicitly. A failure after audio starts makes the output incomplete and exits nonzero.

For an incremental producer, pass NDJSON through stdin:

```bash
printf '%s\n' '{"voice_id":"amy","sample_rate":16000}' '{"text":"First sentence.","flush":true}' '{"text":"Second sentence."}' '{"done":true}' | opal say generate - --incremental -o conversation.pcm
```

## Registered settings

```bash
opal say config setting list
opal say config setting show --name chunk_max_chars
opal say config setting set --name chunk_max_chars --value 200
opal say config setting set --name default_speed --value 1.1
opal say config setting remove --name default_speed
```

`list` returns typed effective values for registered keys, including defaults. `show` and `set` include configured/effective values, source, override and restart metadata, and any masking warnings. Environment overlays win without rewriting the persisted override. `remove` is an idempotent reset to inheritance, not deletion of a setting definition. Unregistered setting writes fail.

To change several settings atomically, save a snapshot, edit its `settings` map, validate it, and load it. There is no collection settings PATCH or legacy `setting` command group.

## Configuration snapshots

```bash
opal say config save
opal say config save --out say-config.json
opal say config save --out say-config.yaml
opal say config validate
opal say config validate --in say-config.json
opal say config load --in say-config.json
opal say config load
```

Without `--out`, save replaces the service-managed snapshot. With `--out`, it asks for `destination: response` and writes JSON/YAML on the **client**, leaving the managed snapshot untouched. Save does not change live configuration or its revision.

Validate checks live configuration when `--in` is omitted, not the managed snapshot. Validation changes neither live state nor snapshot files or adapter/model residency.

Load without `--in` applies the managed snapshot. With `--in`, it applies the provided complete snapshot, without changing the managed one. Load is **full replacement**, never a merge: omitted resource collections and settings overrides are cleared. A successful load advances the live revision once. Invalid/stale loads and failed applications retain the previous configuration. The CLI does not prompt; take a separate snapshot before replacing configuration.

Snapshots contain desired settings overrides, profiles, personas, voice registrations and desired engine configuration. They exclude credentials, model/media binaries, generation history and runtime caches. Install the referenced engines/models separately on another host. Wrong module/version, unknown resource sections and invalid references are rejected.

## Managed service lifecycle

```bash
opal say service start
opal say service start --append
opal say service health
opal say service log --lines 20
opal say service stop
```

These control the tracked server process; the API must be reachable. Starting from a shell can also use `opal-say-svc` directly. Remote lifecycle/configuration mutations require the administrator credential when authentication is enabled.

## Retired callers

The old backup/import/export/restore APIs and CLI group, the entire `info` command group (`info health` and retired `info version/ping/live/ready`), the `engine` and `persona` command groups, flat profile flags, collection settings PATCH and flat `setting` aliases are not compatibility shims. Use `config save/load/validate`, `service health`, named setting options and namespaced profile files instead. Existing numeric database profile IDs upgrade to their string representation; callers must stop coercing IDs to integers.
