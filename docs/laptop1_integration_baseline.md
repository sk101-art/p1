# Laptop 1 Integration Baseline

This document records the Phase 1 baseline **before** any integration changes were made.

## Baseline Git State

- **Baseline commit:** `e5cef9c171bec47725ec3a0e400b2a7963af4129`
- **Baseline branch:** `integration/laptop1-phase1-phase2`
- **Repository root:** `C:\Users\sujay\Downloads\asre-laptop1`
- **Frozen Phase 2 tag:** `phase2-v1.0.0-rc1`
- **Frozen Phase 2 commit:** `ad9c5296a77f8a136daf06f496eaf2c6834214d5`

## Test Command

```
pytest tests/
```

Run with the Phase 1 environment (`.venv-phase1`, Python 3.11).

## Test Results (baseline)

- **Passing test count:** 61
- **Failing test count:** 0

## Canonical Phase 1 Output Path

```
frontend_data/unified_master_dataset.json
```

## Final Dataset-Producing Function

- **Module:** `package_ml_dataset.py`
- **Function:** `package_dataset()`
- **Write call:** `atomic_write_json(OUTPUT_DATASET_FILE, serialized_payload)`
  where `OUTPUT_DATASET_FILE = project_path("frontend_data", "unified_master_dataset.json")`

The write is already atomic (temp file + `os.fsync` + `os.replace`). The integration
notifier is inserted **after** this final write, at the single canonical post-commit boundary.

## Current Dataset Schema / Version

- **Pydantic model:** `UnifiedMasterDataset` (`phase1_schema.py`)
- **`schema_version`:** `"1.0"`
- **`dataset_version`:** `"2.0.0"` (inside `DatasetMeta`)
- **`generated_at`:** ISO-8601 UTC timestamp set at packaging time
- **`metadata`:** `DatasetMeta` with `git_sha`, `source_files`, `processor_version`

## Startup Command

- **PowerShell:** `run.ps1` (tears down, builds, starts Docker stack, launches telemetry daemons)
- **Shell:** `run.sh`

## Dependency Files

- `requirements.txt` (Phase 1 runtime + dev tooling)
- `requirements-phase1.txt` (frozen Phase 2 copy: drain3, aiodocker, pyyaml, pika, requests, docker, psutil, pydantic)
- `requirements-phase2.txt` (frozen Phase 2: pydantic, chromadb, sentence-transformers, fastmcp, psutil, httpx)

## Notes About the Final Write Boundary

- `utils.atomic_write_json` creates a unique temp file via `tempfile.mkstemp`, writes JSON,
  flushes, `os.fsync`, then `os.replace` to the final path. On POSIX it also fsyncs the directory.
- The integration notifier (`integration/laptop1/phase1_notifier.py`) is invoked exactly once,
  immediately after the canonical dataset has been safely committed via `os.replace`.
- The notifier never re-runs Phase 1, never regenerates the dataset, and persists to an outbox
  if the integration service is unavailable (never silently drops the notification).
- Phase 1 detection, filtering, prioritization, Drain3, telemetry, chaos, topology, health scoring,
  schemas and dataset contents are **not** modified by the integration.
