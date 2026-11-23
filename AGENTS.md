# AGENTS.md — opal-say

> Repository-specific guidance for coding agents working on **OPAL SAY**. Read this before modifying contracts, Python code, CLI commands, the web console, configuration, or tests. These rules express architectural intent; they are not evidence that every rule is already implemented.

## 1. Mission and boundaries

OPAL SAY is a **local-first, implementation-neutral text-to-speech service**. It accepts text and delivery settings, selects a compatible installed synthesis engine and registered voice, and returns completed or progressively streamed audio through stable interfaces.

**SAY owns:** synthesis requests, engine adaptation and selection, registered voices, SAY-specific delivery defaults, the SAY portion of reusable profiles, voice bindings used for SAY personas, audio generation/streaming, validation, readiness, and its own configuration.

**SAY does not own:** speech recognition or wake-word detection (LISTEN); reasoning or persona-wide intelligence (THINK); event rules and workflow orchestration (EVENT and other coordination components); visual playback (SHOW); model-file provisioning; or server-side audio-device playback. Its **CLI may play generated audio on the client machine** as an optional convenience. Do not accidentally turn this convenience into a server dependency.

Keep SAY useful without a running THINK, EVENT, BUS, DEVICE, or any other OPAL component. Integrations with those components are optional and must not alter the standalone speech path.

## 2. OPAL's eight design tenets

1. **Boundary Elasticity.** Preserve logical contracts when execution shifts among in-process, standalone, Unix-socket, or network-connected forms. Do not make a deployment boundary a semantic boundary.
2. **Independent Utility.** Each component must provide its useful core capability by itself. SAY must synthesize speech without unrelated OPAL services.
3. **Additive Composition.** Integrations add functionality without changing the meaning of existing standalone operations or requiring modifications to other components.
4. **Economic Accessibility.** Prefer local execution, open interfaces, commodity hardware, and efficient options suitable for modest machines such as Raspberry Pi systems.
5. **Progressive Capability.** Provide a simple, working path first; add advanced engines, streaming, profiles, and integrations incrementally without imposing their costs on minimal deployments.
6. **Interchangeable Parts.** Standardize the interface, not the implementation. Clients must not depend on Piper-, Kokoro-, or device-specific internals.
7. **Discover and Use.** **“Discover what exists. Use what is available. Require nothing else.”** Missing optional engines, models, or services must not disable otherwise usable capabilities.
8. **Extend the Right Surface.** **“If a capability performs an existing operation differently, extend its parameters. If it performs something meaningfully different, extend the API.”** Do not proliferate near-duplicate endpoints or bury different semantics in unrelated flags.

## 3. Contracts and consistency

- Treat **OpenAPI 3.1** as the public source of truth for REST behavior. The suite contract is maintained in `PatMartin/opal-api`, including `openapi/opal_say.yaml` and the shared `opal_kernel.yaml` references. SAY also packages a local contract and publishes `/openapi.json`; reconcile these explicitly.
- **Do not assume contract versions match.** At the time these instructions were authored, `opal-api/openapi/opal_say.yaml` identifies version `0.5.0`, while this repository's README, CLI guide, and user guide describe `0.1.0`. Verify the live/packaged implementation, identify drift, and report it before changing behavior. Do not silently upgrade, downgrade, or regenerate contracts to conceal differences.
- Preserve published REST paths, HTTP methods, `operationId` values, schemas, response media types, status codes, and error behavior unless a breaking change is explicitly approved.
- Reuse suite-wide kernel definitions for health, correlation IDs, errors, configuration, settings, profiles, and service lifecycle where contractually applicable. Do not invent SAY-specific alternatives for common functionality.
- Respect the established OPAL conventions for `X-Opal-Session-Id`, `X-Opal-Transaction-Id`, `X-Opal-Request-Id`, and `X-Opal-Causation-Id`. These identifiers provide tracing, **not authentication**. Report and preserve them on binary and streaming responses where required.
- Use explicit, documented errors; never turn unsupported features or invalid input into silent fallback or apparent success.
- Contract changes should be accompanied by appropriate implementation, CLI, UI, docs, and tests—or explicitly reported as not yet implemented. A schema alone does not prove a feature exists.

## 4. Engine and voice architecture

- **Engine** means a synthesis implementation/capability. **Plugin** means an integration mechanism. Do not conflate the public engine concept with the Python entry-point adapter mechanism.
- Piper and Kokoro are supported examples, not privileged exceptions in shared behavior. Other engines must fit the same public synthesis interface.
- Discover engine adapters that are actually installed; distinguish **installed/discovered**, **administratively enabled**, and **runtime available/healthy** states. A missing optional adapter must not prevent service startup or use of another adapter.
- Engines are implementation-installed resources. The public API does **not** create/delete engine runtimes or install model binaries.
- A voice is a stable OPAL registration referencing a **host-resident** engine-specific voice/model resource. Registration, update, and deletion must not download, upload, overwrite, or remove model files.
- Preserve engine-specific options as explicitly validated/handled adapter data (`engine_options` or its contractual equivalent). Keep portable controls portable: voice, speed, volume, output format, and sample rate.
- Validate compatibility when an explicit engine override is requested for a registered voice. Do not silently select a different incompatible model or voice.
- Keep optional TTS dependencies optional. Importing the API/CLI core should not eagerly load Piper, Kokoro, ONNX models, GPU libraries, or audio playback libraries.
- Keep licensing and attribution for engines, model weights, voices, and redistribution separate. Do not assume a downloadable voice is commercially redistributable.

## 5. Audio generation and streaming

- Preserve the distinction between **completed audio** and **progressive streaming**. Completed output may be WAV or raw PCM as declared by the contract; streaming produces raw PCM, not a WAV container.
- The portable raw PCM format is **signed 16-bit little-endian, mono (`s16le`)**. Preserve the declared sample rate and metadata, including `X-Opal-Sample-Rate` where specified. Never label incompatible bytes as PCM.
- Streaming must emit audio progressively; do not buffer a full synthesis result and misrepresent it as streaming. Keep incremental text input protocols, including NDJSON where supported, explicit and testable.
- Long-text chunking is an internal implementation detail unless publicly documented. It must not lose, duplicate, reorder, or corrupt text/audio at chunk boundaries.
- Enforce configurable, bounded text sizes, output sizes, queues, pending chunks, and timeouts. Honor cancellation and client disconnects; release engine and streaming resources promptly.
- Treat streaming failure after headers or bytes have begun as a **partial/incomplete output**, not a successful full response. Define client-visible failure behavior as far as the transport permits.
- Do not silently clamp invalid speed, volume, sample-rate, format, or adapter options. Validate or return an explicit error.
- Do not add server-side playback, audio-device control, synthesis-history storage, performance-history APIs, or asynchronous job management without an explicit approved contract change.

## 6. Profiles, personas, and defaults

- A **voice** is an installed-resource registration; a **profile** is a reusable configuration preset; a **persona** is a named identity/voice binding. Keep these concepts distinct.
- SAY may change only its own `modules.opal-say` profile compartment. Preserve `shared` and other modules' compartments when loading, reading, editing, or saving portable profiles.
- Respect precedence: explicit request overrides; selected persona/voice resolution as defined by the contract; explicitly selected profile over the active profile; and service defaults as fallback. Do not let defaults for a different engine leak into a request.
- Preserve the distinction between *absent*, *null*, *clear override*, and *explicit value* wherever the schema specifies different meanings. Do not flatten those differences away.
- A configured remote persona owner is **optional**. Preserve local personas and bounded cache/fallback behavior when the owner is unavailable; do not make remote ownership mandatory for synthesis.
- Do not move responsibility for global persona identity, reasoning, or conversation state into SAY merely because a persona may select a voice.

## 7. REST, CLI, and web-console parity

- The Typer CLI (`opal say ...`, `opal-say ...`) and optional React UI are **clients of the same REST API**, not separate engines or alternate persistence systems.
- The long-term suite convention is **CLI derivation from standard OpenAPI**: resource paths determine hierarchy; `operationId` determines semantic action; parameters and schemas determine arguments/options; summaries/descriptions determine help.
- Do **not** introduce `x-opal-cli`, `x-opal-cli-format`, per-operation mapping metadata, or new service-specific command mapping tables. Existing handwritten command code and the `COMMANDS` mapping are migration baselines, **not** a pattern to extend.
- Preserve existing command names unless clearly inconsistent with approved universal conventions. Assess aliases and scripts before removing them. `operationId` changes may break generated clients.
- Treat known CLI-only conveniences (local `--play`, file/stdin input, formatting) as generic **client behavior**, never a reason to add playback to the server API.
- During migration, do not remove existing CLI behavior until an OpenAPI-derived replacement demonstrates parity. Surface exceptional cases—such as machine-only health probes or multiple streaming modes—as explicit design decisions, not hidden mappings.
- Avoid duplicate state or validation logic across FastAPI, CLI, and React. Server contract validation remains authoritative.

## 8. Configuration, security, and operations

- Follow shared OPAL configuration conventions, including save/load/validate snapshots and registered typed settings. Do not independently rename or reinterpret existing published commands or endpoints without approval.
- Preserve precedence among built-in defaults, stored overrides, `.env`, environment variables, profiles, and request fields as explicitly specified for each value. Environment variables must take precedence over corresponding `.env` entries.
- Keep secrets out of committed files, snapshots, generated logs, test fixtures, CLI arguments where avoidable, and API error messages. Use environment-based credential configuration.
- Keep unauthenticated network exposure disabled unless the deployment explicitly permits it. Preserve the existing rule that unauthenticated mode is limited to loopback and non-loopback deployments require authentication and suitable network protections.
- Preserve strong ETag / `If-Match` write preconditions where specified. Reject missing or stale preconditions using contractual status codes; do not silently overwrite concurrent changes.
- Configuration validation must be non-mutating. Configuration load must honor the contract's replacement semantics and avoid partially applying an invalid snapshot.
- Snapshots describe desired state, not secrets, generated audio, model binaries, history, or transient caches. Configuration must remain inspectable and portable without pretending absent models exist.
- Keep liveness separate from readiness. A live service with no usable voice may be alive but not ready to synthesize.
- Events, if supported by an approved contract, remain optional and should be disabled by default. SAY must not require EVENT or BUS to speak.

## 9. Reliability, performance, and maintainability

- Prefer simple, bounded local execution before distributed scheduling or resource managers. Do not add a global GPU scheduler or orchestration framework inside SAY.
- Bound concurrency, engine acquisition times, buffering, memory, and cancellation behavior; ensure an unavailable or slow engine cannot indefinitely block unrelated work.
- Avoid reinitializing models unnecessarily, but make model residency and cache ownership explicit. Never claim GPU acceleration, availability, or real-time performance without measurements.
- Preserve stable request and event correlation while avoiding audio/text contents in routine logs. Never log bearer credentials or sensitive request bodies by default.
- No silent retries of state-changing operations. No unbounded background tasks, queues, threads, or retained audio buffers.
- Prefer adding a generic adapter or validation rule over inserting more Piper/Kokoro-specific branches into common code.
- Do not broaden scope or refactor unrelated modules during a focused change. Challenge unnecessary complexity and explain tradeoffs rather than agreeing reflexively.

## 10. Testing and evidence

- Run relevant checks when dependencies are available; the repository declares `pytest` and Ruff tooling. Typical commands:

  ```bash
  python -m pytest -q
  ruff check .
  ```

- Use mocked or fake engines for fast deterministic tests, and clearly separate them from optional hardware/model integration tests. Do not download large models during default CI or require speaker hardware for ordinary unit tests.
- Test engine discovery and missing-engine fallback; voice resolution and compatibility; profile/persona precedence and foreign-compartment preservation; configuration ETags/snapshots; authentication; response media types; WAV headers; PCM sample rate and byte alignment; streaming progress/backpressure/disconnect; CLI/REST parity; and contract drift.
- For every bug fix, add a regression test that fails before the change when feasible. Avoid claiming full integration coverage from isolated mocks.
- If tests cannot run, state the exact blocker. **Never invent test counts or claim a test passed without executing it.**
- For potentially breaking or multi-step work, report: files changed, behavior changed, estimated defect risk (%), exact tests and passed/failed/skipped counts, independent verification commands, testing gaps, remaining issues, and proposed next step.
- Stop for approval before breaking REST/CLI compatibility, changing engine semantics, adding dependencies, or expanding SAY's ownership into another OPAL component.

## 11. First checks before editing

1. Read `README.md`, `docs/USER_GUIDE.md`, `docs/CLI.md`, and the relevant service/engine code.
2. Compare this repository's packaged OpenAPI contract and `/openapi.json` behavior with `PatMartin/opal-api/openapi/opal_say.yaml` and shared kernel definitions. Report drift; do not guess which revision is implemented.
3. Identify the actual source of truth for the requested operation and its existing tests.
4. Explain the smallest change that meets the requirement without violating boundaries above.
5. Implement and verify only the approved scope; preserve working behavior and report uncertainties honestly.

**Guiding test:** Can a minimal, standalone SAY installation still turn text into correct audio through a stable API, while optional engines, clients, personas, and OPAL integrations remain replaceable and non-mandatory?
