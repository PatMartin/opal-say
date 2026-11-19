# OPAL Say contract notes

The pinned source is `openapi/opal_say_v0.1.0.yaml` and its external reference tree, including `openapi/shared/opal_kernel.yaml`. These sources are read-only migration inputs. Contract version **0.1.0** does not change the declared **`/api/v1`** prefix.

## Publication and parity

```bash
python tools/bundle_contract.py
python tools/bundle_contract.py --check
npm --prefix spa run gen-ops
```

`src/opal_say/contract/opal_say.yaml` is the generated self-contained bundle, including transitively referenced kernel components. The application serves that document at `/openapi.json`; it does not substitute FastAPI-generated schemas. `tests/test_contract.py` checks source/bundle/served equality, exact route/operation parity, and a response-schema-checked walkthrough of all **39 operations**. The SPA derives its operation map from the same bundle. CLI operation IDs bind to contract-derived paths.

The CLI covers 37 operations. Kernel liveness/readiness probes explicitly have no CLI binding; humans use `info health` instead. The target defines no old flat settings command aliases. The deprecated generation field `persona` remains supported as an alias of canonical `persona_id`.

## Shared transport

All errors use the kernel `OpalError` envelope. Transaction and request IDs are generated when absent; session and causation are propagated only when supplied. The four OPAL context headers accept printable ASCII tokens of 1–128 characters. Downstream owner requests propagate transaction/session context, create a new request ID and use the parent request ID as causation.

Configuration revisions are strong whole-configuration ETags of the form `"r<N>"`. Configuration writes compare `If-Match` and apply the mutation under the same store write lock. Missing headers return 428, well-formed stale tags 412, and malformed/weak/list/wildcard tags 422. Successful configuration responses expose the current/resulting ETag; CORS exposes it and the OPAL headers.

Authorization is explicitly configurable. `service.token_env` and `service.admin_token_env` name environment variables, never embedded secrets. Either credential may read; only the administrator may mutate configuration or control the service. Authentication may be disabled only on loopback. The CLI obtains a bearer credential from `OPAL_SAY_TOKEN`; the SPA keeps entered credentials in memory only.

## Profiles, personas and stored data

Profiles have opaque string IDs and closed top-level `shared`/`modules` documents. Say defaults live in `modules.opal-say`; foreign compartments round-trip without interpretation. Updates are restricted to Say-owned fields, with field-level null removal except `sample_rate: null`, which remains a valid stored value. Setting `is_active: false` does not deselect the active profile.

SQLite upgrades preserve old numeric IDs as strings, move flat synthesis defaults into the Say compartment, and permit nullable persona voice bindings. The upgrade is transactional and retryable; fixture tests reopen the migrated database and inject a failed upgrade.

Personas may be unbound. `origin` is an optional public provenance field. With an owner configured, a same-ID owner definition takes precedence while the local record is retained; cache records are separate from local desired configuration. Conditional owner refresh uses its ETag. Generation supports both persona selector spellings on all three audio operations, rejects conflicting selectors, and preserves standalone/offline behavior.

## Desired configuration snapshots

`POST /config/save`, `/config/load`, and `/config/validate` replace the backup/archive/import/export/restore surface. Snapshots use format version `1.0.0`, module `opal-say`, native JSON setting overrides and the declared `profiles`, `personas`, `voices`, and `engines` collections. They exclude credentials, model/media binaries, generated history and runtime caches.

Save defaults to the managed location; `destination: response` leaves it untouched. Save never mutates live configuration or its revision. Managed replacement uses an atomic rename. Validate without a candidate checks live configuration, not the managed snapshot, and is side-effect-free. Load is a complete atomic replacement, not a merge, requires `If-Match`, advances the revision once and does not overwrite the managed snapshot. Database/runtime preparation failures roll back. CLI `--out`/`--in` files are client-side JSON/YAML; the SPA accepts JSON candidates.

## Clean cutover

The retired `/info/*` and `/backup/*` routes are absent, as is collection settings PATCH. Settings now expose registered typed effective/default/configured values and provenance; DELETE is an idempotent override reset. Identity and diagnostics come from aggregate health, with engine details nested under `details`.

Voice operation IDs are `list_voices`, `create_voice`, `show_voice`, `update_voice`, `remove_voice`, `validate_voice`, and `test_voice`; engine validation is `validate_engine`. Speech operation IDs are `generate_speech`, `stream_speech`, and `stream_speech_incremental`. Incremental NDJSON is part of the pinned target, not a local pending proposal.

Two source-prose inconsistencies remain upstream and were not edited: the service suite-version label differs from the kernel label, and persona CLI prose says record revision while the shared `IfMatch` definition requires the complete logical configuration revision. The concrete schemas/operation semantics and shared whole-configuration precondition govern this implementation.

For current commands and examples, see [CLI reference](CLI.md), [user guide](USER_GUIDE.md), and the served contract.
