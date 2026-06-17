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

Planned command/status topics:

- `device.status`
- `feedback.status`
- `runtime.metrics`

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

Current expected result:

```text
81 passed
```

## Documentation Rules

- If architecture changes, update `docs/ARCHITECTURE.md`.
- If topics change, update `docs/EVENTBUS_SPEC.md`.
- If project status changes, update `docs/ROADMAP.md` and root `TODO.md`.
- Keep test counts and file structure examples in docs synchronized with the actual repo.

## Forbidden Patterns

- No direct `DevicePanel -> ScopeApp._on_art_config` control path.
- No runtime polling of UI state as the long-term mechanism for measurement specs.
- No new long-running work in the Qt main thread.
- No broad rewrites unrelated to the requested change.
