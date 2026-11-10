# OPAL Say v1.7.1 migration ledger

Contract: `openapi/opal_say_v1.7.1.yaml` + `openapi/shared/opal_kernel.yaml`
(authoritative; do not edit — amendments go upstream to `opal-api`).

## Normative deltas implemented

| Delta | Implementation |
|---|---|
| `service` tag + `/api/v1/service/start|stop|log` (kernel pathItems, `OpalServiceStartOptions`/`OpalServiceStatus`/`OpalServiceLogTail`, `ServiceLogLines`) | `ServiceProcessManager` (`service/lifecycle.py`): pidfile-tracked detached `python -m opal_say` under `logs/`; routes in `api/routes.py` defer self-termination to a BackgroundTask when managed pid == self; early-death poll -> `500 operation_failed`. `__main__` honors `OPAL_SAY_PID_FILE` / `OPAL_SAY_WAIT_FOR_PID`. |
| `x-opal-cli` bindings (settings + service) | `config setting list|show|set|update|remove` canonical (+ `config setting patch`, `setting get`, `setting delete` aliases); `service start|stop|log` REST-first with local fallback. |
| `PersonaRead.origin` removed | Field dropped from the wire DTO; store column kept for owner-sync conflict rules. |

## Cosmetic deltas (verified no-ops)

- `persona` -> kernel `OpalPersonaRef` (same pattern as existing DTO constraint).
- `GenerateRequest`/`GenerateStreamRequest` -> `GenerateRequestBase` `allOf` +
  `unevaluatedProperties: false` (equivalent to `extra="forbid"`).
- History pagination note (cursor -> bounded `limit`): Say has no history endpoints.
- `x-opal-persona.profiles_vs_personas` doc block; generate examples; `service` tag.

## Commits

1. `bb96cb3` drop `PersonaRead.origin`
2. `9fd8815` lifecycle plumbing (`ServiceProcessManager`, `__main__` envs, `ServiceConfig.log_dir`/`source_path`)
3. `3f107f6` adopt contract v1.7.1 (bundle regen + resolver snapshot fix, routes, CLI service group, SPA Service panel, tests)
4. `177438f` canonical `config setting` CLI tree
5. (this commit) docs + validation sync

## Decisions / notes for upstream

- "The service's server process" = the pidfile-tracked managed child only; REST
  `service stop` never kills an unmanaged foreground server. The managed child
  launched against an occupied port dies and `start` returns `500 operation_failed`.
- No `GET /api/v1/service/` status op exists — a caller must mutate to learn state.
- REST `service start` cannot cold-start (nothing is listening); the CLI covers it
  via local spawn fallback.
