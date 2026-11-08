# OPAL Say contract notes

Authoritative contract: `openapi/opal_say_v1.6.0.yaml` together with the shared
OPAL kernel `openapi/shared/opal_kernel.yaml`.

## Kernel split

The root contract defines the Say-specific operations inline (voice CRUD,
voice validate/test, engine validate, persona CRUD/validate, generate/stream)
and pulls the suite-standard routes — engine, profile, setting, health, backup,
and the `info` namespace (`info`, `info_ping` pathItems plus shared
`ServiceInfo`/`LivenessRead` schemas) — in from the kernel via external `$ref`
pathItems. The kernel is a superset shared across OPAL services; pathItems a
service does not implement are simply not referenced.

`src/opal_say/contract/opal_say.yaml` is the packaged, self-contained bundle
produced by:

```bash
python tools/bundle_contract.py
```

`tests/test_contract.py` asserts the packaged file equals a fresh bundle of the
`openapi/` sources, so regenerate it whenever the root contract or kernel
changes.

## Optimistic concurrency (If-Match)

Mutable configuration writes require a strong `If-Match` header carrying the
current ETag of the complete logical configuration (`"r<N>"`, where `N` is the
SQLite `config_revision`). Any configuration mutation bumps the revision, so a
tag goes stale after any write anywhere in the config.

- Missing `If-Match` → `428 precondition_required`
- Stale value → `412 precondition_failed`
- Malformed (weak tag, `*`, list, unquoted) → `422`

Reads of configuration resources return the current `ETag` header; the CLI
client fetches it automatically for the preconditioned operations listed in
`opal_say.cli.client.PRECONDITION_OPS` and retries once on `412`.

## Personas

`/api/v1/persona/` is a Say-local catalog of named persona — a `persona_id`,
display name, and a bound registered `voice_id` (kept deliberately flat; the
shape is designed so a future cross-service persona authority can serve the
same identifiers). Generation requests accept `persona` on both
`/api/v1/generate` and `/api/v1/generate/stream`:

- the persona supplies a voice default: explicit `voice_id` > persona voice >
  profile voice > service defaults;
- unknown or disabled persona → `422`;
- persona create/update/delete carry the same If-Match preconditions as other
  configuration mutations; a persona's bound `voice_id` must resolve at write
  time (`404` otherwise), and a voice cannot be deleted while persona
  reference it (`409 voice_in_use`).

## v1.6.0 surface conventions

- **Singular resources.** Collection paths are singular — `/api/v1/voice/`,
  `/api/v1/engine/`, `/api/v1/profile/`, `/api/v1/setting/`, `/api/v1/persona/`
  — mounted onto the same kernel pathItems the other OPAL services share.
- **`info` namespace.** Service identity lives under `/api/v1/info/`
  (`opal_info_get`) with a liveness probe at `/api/v1/info/ping`
  (`opal_info_ping`); kernel `/api/v1/health/*` remains the readiness surface.
- **operationId convention.** `opal_{resource}_{verb}` throughout: collection
  ops use `{resource}_list`, item ops `{resource}_{get|patch|delete|verb}`
  (`opal_voice_list`, `opal_voice_get`, `opal_profile_activate`,
  `opal_setting_list`). Say-local ops that previously carried generated
  FastAPI-style ids (`list_voice_api_v1_voice__get`) were renamed to match.
- **`persona validate`** (`opal_persona_validate`): reports whether a persona
  resolves — registered + enabled, bound voice present and valid against its
  engine. Response is the kernel `OpalValidationReport`.
- **`voice list ?language=`**: optional prefix filter on stored language codes
  (case-insensitive; `en` matches all English locales, `en_US` only US).
