# Laptop1 — Phase 1 ↔ Phase 2 Integration

This document describes the durable local orchestration bridge that connects the
existing **Phase 1** pipeline (incident detection / filtering / prioritization /
Drain3 / telemetry / chaos / topology / health / dataset packaging) to the
**frozen Phase 2** release (`tag phase2-v1.0.0-rc1`, commit
`ad9c5296a77f8a136daf06f496eaf2c6834214d5`).

> **Scope invariant:** This bridge does NOT modify Phase 1 internals and does NOT
> modify frozen Phase 2 internals. It only *imports* the frozen
> `process_phase1_dataset` entry point and persists its results.

---

## 1. Architecture

```
 Phase 1 (unchanged)                 Integration bridge (this repo)        Phase 2 (frozen)
 ───────────────────                 ────────────────────────────         ────────────────
 package_ml_dataset.py  ──notify──▶  phase1_notifier.py (stdlib only)
   writes unified_master_dataset.json   │  POST /v1/notify/phase1-dataset
                                       ▼
                              main.py (FastAPI, 127.0.0.1:8102)
                                       │
                                       ▼
                              IntegrationController.run_once()
                                       │
                          ┌────────────┴─────────────┐
                          ▼                          ▼
                  StateStore (SQLite)        Phase2Runtime.warm()/process()
                  runtime/state/*.db              │
                          ▲                       ▼
                          │              process_phase1_dataset(raw, memory)
                          │                       │
                          │                       ▼
                          └──── insert_result ◀── Phase2BatchOutput (incidents[])
                                  (durable summaries)
```

### Components

| Module | Responsibility |
| --- | --- |
| `integration/laptop1/phase1_notifier.py` | Stdlib-only notifier. Posts a `phase1_dataset_packaged` event to the local service. Refuses to send to any non-loopback host. |
| `integration/laptop1/main.py` | FastAPI composition root. Routes: `POST /v1/notify/phase1-dataset`, `GET /health`, `GET /status`, `POST /v1/trigger`. Binds **127.0.0.1 only**. |
| `integration/laptop1/controller.py` | `IntegrationController.run_once()` — the durable brain. Loads dataset (freshness guard), creates a PENDING run, drives Phase 2, persists per-incident summaries, marks SUCCEEDED/FAILED. |
| `integration/laptop1/state_store.py` | SQLite state store (WAL, `synchronous=FULL`, `foreign_keys=ON`, `busy_timeout=5000`). Tables: `runs`, `phase2_results`. Self-healing schema migration. |
| `integration/laptop1/phase2_runtime.py` | Wraps the frozen `process_phase1_dataset` + `IncidentMemory`. Warms embedder + chromadb once. |
| `integration/laptop1/recovery.py` | `RecoveryManager.recover()` — re-drives any PENDING/RUNNING runs left by a crashed process. Idempotent. |
| `integration/laptop1/health.py` | `HealthService.status()` — `PipelineStatus` snapshot. |
| `integration/laptop1/contract_registry.py` | Verifies frozen Phase 2 files match `frozen_phase2_manifest.json` byte-for-byte (blob SHA). Flags CHANGED / MISSING / UNEXPECTED. |
| `integration/laptop1/artifact_manager.py` | Dataset snapshots + run summaries. |
| `integration/laptop1/models.py` | Pydantic boundary contracts (`extra="forbid"`). |
| `integration/laptop1/config.py` | Paths, bind host/port, notify/health/status/trigger paths, recovery flag, poll interval. |
| `integration/frozen_phase2_manifest.json` | 41 frozen files + tag + commit. |
| `integration/verify_frozen_phase2.py` | Standalone verifier (exit 0 only when all frozen files valid). |

---

## 2. Safety invariants

1. **Loopback only.** The notifier refuses any host other than `127.0.0.1`. The
   service binds `127.0.0.1` (never `0.0.0.0`). No traffic leaves the laptop.
2. **Frozen Phase 2 is read-only.** The contract registry fails the build/verify
   if any frozen file is CHANGED, MISSING, or if unexpected files appear inside
   `phase2/`, `tests/phase2/`, or `contracts/`.
3. **Durable state.** Every run transition (PENDING → RUNNING → SUCCEEDED/FAILED)
   is written to SQLite before/after the heavy Phase 2 call, so a crash mid-run
   leaves a recoverable record.
4. **Freshness guard.** Datasets older than `LAPTOP1_DATASET_MAX_AGE_SECONDS`
   (default 86400) are rejected as stale before any Phase 2 work begins.
5. **Crash recovery.** On startup (and on a timer if configured),
   `RecoveryManager.recover()` re-drives incomplete runs. Re-running a run simply
   overwrites its durable record (Phase 2 is deterministic per dataset).
6. **Stdlib-only notifier.** `phase1_notifier.py` uses only `urllib.request` so it
   can run inside the Phase 1 environment without extra dependencies.

---

## 3. Run / Stop / Verify

### Prerequisites
- Python 3.11 with Phase 1 + Phase 2 dependencies installed in `.venv-phase2`.
- The frozen Phase 2 tag already imported under `phase2/`.

### Start the service
```powershell
make laptop1-run
# or directly:
powershell -File integration/laptop1/run-laptop1-integration.ps1
```
This warms the Phase 2 runtime, runs recovery, and starts the FastAPI service on
`127.0.0.1:8102`.

### Stop the service
```powershell
make laptop1-stop
powershell -File integration/laptop1/stop-laptop1-integration.ps1
```

### Verify
```powershell
make laptop1-verify
powershell -File integration/laptop1/verify-laptop1-integration.ps1
```
This runs `integration/verify_frozen_phase2.py` and the integration test suite.

### Manual trigger
```powershell
curl.exe -X POST http://127.0.0.1:8102/v1/trigger `
  -H "Content-Type: application/json" `
  -d '{"dataset_path":"C:/Users/sujay/Downloads/asre-laptop1/frontend_data/unified_master_dataset.json"}'
```

### Health / status
```powershell
curl.exe http://127.0.0.1:8102/health
curl.exe http://127.0.0.1:8102/status
```

---

## 4. Makefile targets

| Target | Action |
| --- | --- |
| `laptop1-run` | Start the integration service. |
| `laptop1-stop` | Stop the integration service. |
| `laptop1-verify` | Run frozen-contract verification + integration tests. |

---

## 5. Latency characteristics (Phase 17 measurement)

Measured on a warm runtime against a 10-incident synthetic dataset:

| Metric | Value |
| --- | --- |
| Cold first run (includes chromadb warm) | ~23.3 s |
| Steady-state p50 | ~1.33 s |
| Steady-state min | ~1.28 s |
| Steady-state p95 (incl. cold) | ~18.9 s |

The runtime is warmed **once at startup**; subsequent `run_once` calls reuse the
embedder + chromadb collection, so steady-state latency is ~1.3 s per dataset.

---

## 6. Testing

Integration tests live under `tests/integration/`:

- `test_state_store.py` — CRUD, transitions, pending/last queries, results
  round-trip, status counts, schema migration.
- `test_phase1_notifier.py` — success path, non-loopback refusal, graceful
  failure, payload shape.
- `test_contract_registry.py` — clean-tree pass, changed-file detection.
- `test_controller.py` — `run_once` success + persistence, bad-dataset failure,
  freshness guard, recovery re-drive.
- `test_health.py` — `PipelineStatus` fields under various states.

Run with the Phase 2 venv:
```powershell
.venv-phase2\Scripts\python.exe -m pytest tests/integration -q
```

---

## 7. Directory layout

```
integration/
  frozen_phase2_manifest.json
  verify_frozen_phase2.py
  laptop1/
    main.py
    controller.py
    state_store.py
    phase2_runtime.py
    recovery.py
    health.py
    contract_registry.py
    artifact_manager.py
    phase1_notifier.py
    models.py
    config.py
    measure_latency.py
    run-laptop1-integration.ps1
    stop-laptop1-integration.ps1
    verify-laptop1-integration.ps1
    README.md
    requirements-integration.txt
tests/
  integration/
    conftest.py
    fixtures/fixture_loader.py
    test_state_store.py
    test_phase1_notifier.py
    test_contract_registry.py
    test_controller.py
    test_health.py
runtime/
  state/laptop1_pipeline.db   (gitignored)
```
