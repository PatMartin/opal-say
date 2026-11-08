# OPAL Say contract notes

Authoritative contract: `openapi/opal_say_v1.5.0.yaml` together with the shared
OPAL kernel `openapi/shared/opal_kernel.yaml`.

## Kernel split

The root contract defines the Say-specific operations inline (voices CRUD,
voice validate/test, engine validate, generate/stream, root, healthz) and pulls
the suite-standard routes — engines, profiles, settings, health, backup — in
from the kernel via external `$ref` pathItems. The kernel is a superset shared
across OPAL services; pathItems a service does not implement are simply not
referenced.

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
