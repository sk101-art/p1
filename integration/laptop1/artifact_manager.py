"""Artifact manager: snapshots run artifacts under runtime/artifacts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from .config import ARTIFACT_DIR


class ArtifactManager:
    def __init__(self, artifact_dir: Optional[Path] = None) -> None:
        self.artifact_dir = Path(artifact_dir) if artifact_dir else ARTIFACT_DIR

    def snapshot_dataset(self, run_id: str, dataset_path: str | Path) -> Optional[Path]:
        """Copy the canonical dataset into the run artifact folder."""
        src = Path(dataset_path)
        if not src.exists():
            return None
        run_dir = self.artifact_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        dst = run_dir / "unified_master_dataset.json"
        shutil.copy2(src, dst)
        return dst

    def write_summary(self, run_id: str, summary: dict) -> Path:
        run_dir = self.artifact_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        out = run_dir / "run_summary.json"
        out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return out


__all__ = ["ArtifactManager"]
