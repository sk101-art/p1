"""Configuration for the loopback-only Laptop 1 integration service."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_DATASET_FILE = REPO_ROOT / "frontend_data" / "unified_master_dataset.json"
STATE_DB_PATH = REPO_ROOT / "runtime" / "state" / "laptop1_pipeline.db"
RUNTIME_LOG_DIR = REPO_ROOT / "runtime" / "logs"
ARTIFACT_DIR = REPO_ROOT / "runtime" / "artifacts"

BIND_HOST = os.getenv("LAPTOP1_BIND_HOST", "127.0.0.1")
BIND_PORT = int(os.getenv("LAPTOP1_BIND_PORT", "8102"))
NOTIFY_PATH = "/v1/notify/phase1-dataset"
HEALTH_PATH = "/health"
HEALTHZ_PATH = "/healthz"
READYZ_PATH = "/readyz"
STATUS_PATH = "/status"
TRIGGER_PATH = "/v1/trigger"
JOBS_PATH = "/internal/v1/jobs/{run_id}"
RECOVERY_ON_STARTUP = os.getenv("LAPTOP1_RECOVERY_ON_STARTUP", "1") == "1"


def ensure_runtime_dirs() -> None:
    for path in (STATE_DB_PATH.parent, RUNTIME_LOG_DIR, ARTIFACT_DIR):
        path.mkdir(parents=True, exist_ok=True)


__all__ = [name for name in globals() if name.isupper()] + ["ensure_runtime_dirs"]
