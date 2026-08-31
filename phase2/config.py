"""Centralized configuration for Phase 2 incident memory and processing."""

from __future__ import annotations

import os
from pathlib import Path

# Provisional default threshold for semantic similarity retrieval.
# Production quality certification remains BLOCKED pending human-reviewed labels.
PROVISIONAL_MIN_SIMILARITY: float = float(
    os.getenv("PHASE2_MIN_SIMILARITY", "0.40")
)
DEFAULT_TOP_K: int = int(os.getenv("PHASE2_TOP_K", "5"))
DEFAULT_TIMEOUT_SECONDS: float = float(os.getenv("PHASE2_TIMEOUT_SECONDS", "15.0"))

DEFAULT_CHROMA_DIR: Path = Path(
    os.getenv("PHASE2_CHROMA_DIR", "chroma_memory_db")
)
DEFAULT_COLLECTION_NAME: str = os.getenv(
    "PHASE2_COLLECTION", "sre_incident_memory_v3"
)
DEFAULT_EMBED_MODEL: str = os.getenv(
    "PHASE2_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)

VALIDATION_ARTIFACTS_DIR: Path = Path("artifacts/phase2_validation")
BACKUP_DIR: Path = Path("artifacts/phase2_backups")
LOCK_FILE_PATH: Path = DEFAULT_CHROMA_DIR.parent / "chroma_memory_db.lock"

# Metadata allowlist: structured keys preserved without free-text secret redaction
STRUCTURED_METADATA_ALLOWLIST: set[str] = {
    "incident_id",
    "target_service",
    "severity",
    "priority_score",
    "occurrence_count",
    "fingerprint",
    "schema_version",
    "dataset_version",
    "dataset_generated_at",
    "trace_ids_json",
}
