# laptop1 Phase1 <-> frozen Phase2 Integration

This package contains the durable local orchestration layer that connects the
existing Phase 1 implementation (in the worktree root) to the frozen Phase 2
release (`phase2-v1.0.0-rc1`).

## Layout
- `integration/laptop1/main.py` — entrypoint (uvicorn on 127.0.0.1:8102)
- `integration/laptop1/api.py` — local HTTP API (loopback only)
- `integration/laptop1/models.py` — Pydantic event contracts (extra="forbid")
- `integration/laptop1/state_store.py` — SQLite durable state (WAL)
- `integration/laptop1/phase2_runtime.py` — warm Phase 2 runtime
- `integration/laptop1/controller.py` — orchestration controller
- `integration/laptop1/recovery.py` — crash recovery
- `integration/laptop1/health.py` — health endpoint
- `integration/laptop1/phase1_notifier.py` — Phase 1 post-commit notifier (stdlib only)
- `integration/verify_frozen_phase2.py` — frozen-file integrity verifier
- `integration/frozen_phase2_manifest.json` — authoritative blob SHAs

## Scripts
- `run-laptop1-integration.ps1` — start the service
- `stop-laptop1-integration.ps1` — stop the service
- `verify-laptop1-integration.ps1` — verify frozen files + health + state

## Safety invariants
- Integration service binds **127.0.0.1:8102** only (never 0.0.0.0).
- Phase 1 notifier uses **Python stdlib only** (urllib.request).
- Frozen Phase 2 files are never modified; integrity is verified on every run.
- Durable state lives in `runtime/state/laptop1_pipeline.db` (gitignored).
