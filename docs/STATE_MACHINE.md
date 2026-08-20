# Pipeline State Machine

```
IDLE
  ↓
PROJECT_READY
  ↓
RENDERING
  ↓
TRANSCRIBING
  ↓
EXTRACTING_FIGURES
  ↓
ASSEMBLING
  ↓
CLEANING
  ↓
VALIDATING
  ↓
REVIEW_REQUIRED (optional)
  ↓
COMPLETED
```

Interrupt states: `PAUSED`, `CANCELLED`, `ERROR`

## Page overview (`pages.status`)

`WAITING`, `RENDERING`, `RENDERED`, `PROCESSING`, `SUCCESS`, `FAILED`, `NEEDS_REVIEW`, `SKIPPED`

## Per-stage states (`page_stage_states`) — Phase 3+

Stages: `render`, `transcribe`, `figures`, `assemble`, `clean`, `validate`

Status (`StageStatus`): `waiting`, `running`, `success`, `cached`, `failed`, `cancelled`, `stale`

Prefer `page_stage_states` when deciding whether a stage is complete.

## Ollama Runtime State (Phase 2)

```
NOT_FOUND → STOPPED → STARTING → READY
                              ↘ ERROR
READY → STOPPING → STOPPED
```
