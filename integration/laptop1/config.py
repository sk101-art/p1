"""Configuration for the laptop1 integration service.

All paths resolve against the repository root (the git worktree). The service
binds ONLY to 127.0.0.1 on the configured port (default 8102) — never
0.0.0.0.
"""

from __future__ import annotations

import os
from pathlib import Path

# Repository root: integration/laptop1/ -> repo root (two levels up).
REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent

CANONICAL_DATASET_FILE: Path = REPO_ROOT / "frontend_data" / "unified_master_dataset.json"
STATE_DB_PATH: Path = REPO_ROOT / "runtime" / "state" / "laptop1_pipeline.db"
RUNTIME_LOG_DIR: Path = REPO_ROOT / "runtime" / "logs"
ARTIFACT_DIR: Path = REPO_ROOT / "runtime" / "artifacts"

BIND_HOST: str = os.getenv("LAPTOP1_BIND_HOST", "127.0.0.1")
BIND_PORT: int = int(os.getenv("LAPTOP1_BIND_PORT", "8102"))
NOTIFY_PATH: str = "/v1/notify/phase1-dataset"
HEALTH_PATH: str = "/health"
STATUS_PATH: str = "/status"
TRIGGER_PATH: str = "/v1/trigger"

RECOVERY_ON_STARTUP: bool = os.getenv("LAPTOP1_RECOVERY_ON_STARTUP", "1") == "1"
POLL_INTERVAL_SECONDS: int = int(os.getenv("LAPTOP1_POLL_INTERVAL_SECONDS", "5"))


def ensure_runtime_dirs() -> None:
    for p in (STATE_DB_PATH.parent, RUNTIME_LOG_DIR, ARTIFACT_DIR):
        p.mkdir(parents=True, exist_ok=True)


__all__ = [
    "REPO_ROOT",
    "CANONICAL_DATASET_FILE",
    "STATE_DB_PATH",
    "RUNTIME_LOG_DIR",
    "ARTIFACT_DIR",
    "BIND_HOST",
    "BIND_PORT",
    "NOTIFY_PATH",
    "HEALTH_PATH",
    "STATUS_PATH",
    "TRIGGER_PATH",
    "RECOVERY_ON_STARTUP",
    "POLL_INTERVAL_SECONDS",
    "ensure_runtime_dirs",
]
