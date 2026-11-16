# AGENTS.md

Rules for AI agents working in this repository.

## Project Shape

This project is a PyQt6 + EventBus based digital oscilloscope for ART USB acquisition hardware.

The intended runtime architecture is:

```text
hardware callback -> frame.raw -> MeasurementProcessor -> frame.fitted
                                      |
                                      +-> UIBridge -> Qt UI
                                      +-> FeedbackManager -> FeedbackWorker

UI command -> EventBus command topic -> runtime worker -> state/data topic -> UI
```

## Non-Negotiable Architecture Rules

- Runtime and business events must use `scope.runtime.EventBus`.
- Qt signals are for local UI interactions and Qt main-thread bridging only.
- Hardware configuration changes must go through `config.change` and `ConfigWorker`.
- Acquisition callbacks must only create/publish `RawFrame` data and perform minimal bridge polling.
- Measurement computation must live in `MeasurementProcessor`.
- PID math must live in `PidController`.
- Feedback runtime behavior must live in `FeedbackManager` and `FeedbackWorker`.
- Do not reintroduce `scope/processing/`.
- Do not reintroduce `FeedbackSlot`, `DataSubscription`, `PidFeedbackSlot`, or `dispatch_raw()`.
- Do not add blocking I/O to Qt UI slots or feedback processing.

## EventBus Topics

Current required topics:

- `frame.raw`
- `frame.fitted`
- `config.change`
- `measurement.specs.changed`
- `measurement.remove`
- `feedback.worker.command`
- `feedback.status`
- `runtime.metrics`

`frame.fitted` 载荷 `FittedSnapshot` 含 `invalid_reasons`（tag → 原因）；比值测量分母接近 0 或源无效时不产出值而登记原因，`FeedbackManager` 据此让对应 worker 自动暂停。

Planned status topics:

- `device.status`

## UI Rules

- UI panels may publish business commands to EventBus.
- UI panels must not directly call hardware methods.
- UI panels must not directly mutate runtime workers except through approved command APIs.
- `UIBridge` is the bridge from runtime frame data into Qt display updates.
- Keep local widget interactions as Qt signals/slots.

## Config Rules

- Device runtime configuration is `scope.hardware.DeviceConfig`.
- JSON persistence must explicitly serialize/deserialize dataclasses.
- Loading a config file may update UI state, but applying hardware changes must still publish `config.change`.

## Testing Rules

Use the project conda environment, not the global Python installation:

```powershell
& .\.venv\python.exe -m pytest -q
```

Current test baseline and evidence: [`doc/current/NOW.md`](doc/current/NOW.md).

If the local conda cache/temp directories deny access, use the dedicated temporary-directory command in [`doc/guides/ENVIRONMENT.md`](doc/guides/ENVIRONMENT.md).

## Documentation Rules

- Documentation navigation: [`doc/README.md`](doc/README.md).
- Current committed project state and test evidence: [`doc/current/NOW.md`](doc/current/NOW.md), the single current-state authority.
- Metadata, classification, lifecycle and authority: [`doc/governance/DOCUMENT_METADATA.md`](doc/governance/DOCUMENT_METADATA.md).
- If architecture changes, update `doc/project/ARCHITECTURE.md`.
- If topics change, update `doc/specs/EVENTBUS_SPEC.md` and the topic list above.
- If feedback behavior changes, update `doc/specs/FEEDBACK_SPEC.md`.
- If project status changes, update `doc/current/NOW.md`; maintain outstanding work only in `doc/status/ACTIVE_TODO.md`. Update `doc/status/ROADMAP.md` when development direction changes.
- New documents must have governance front matter and an index entry. Root documentation is limited to README.md, AGENTS.md and optional CONTEXT.md; dependency manifests remain runtime assets.
- Keep archived/superseded/deprecated material out of current authority. Preserve history under `doc/archive/`; dated records belong under `doc/status/`.
- Do not refresh `last_verified` merely because a file moved; separate offline evidence and hardware observations.
- Pure documentation changes require governance validation and `git diff --check`:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate_doc_governance.ps1
git diff --check
```

## Forbidden Patterns

- No direct `DevicePanel -> ScopeApp._on_art_config` control path.
- No runtime polling of UI state as the long-term mechanism for measurement specs.
- No new long-running work in the Qt main thread.
- No broad rewrites unrelated to the requested change.
