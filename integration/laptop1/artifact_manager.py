"""Crash-safe, immutable run artifacts for the Laptop 1 boundary."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from .config import ARTIFACT_DIR

_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ArtifactManager:
    def __init__(self, artifact_dir: Optional[Path] = None,
                 phase3_writer: Optional[Callable[[str | Path, str | Path], list[Path]]] = None) -> None:
        self.artifact_dir = Path(artifact_dir) if artifact_dir else ARTIFACT_DIR
        self.phase3_writer = phase3_writer

    def _run_dir(self, run_id: str) -> Path:
        if not _SAFE_RUN_ID.fullmatch(run_id):
            raise ValueError("run_id contains unsafe path characters")
        return self.artifact_dir / "runs" / run_id

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".{path.name}.{uuid.uuid4().hex}.staging")
        try:
            with open(staging, "xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(staging, path)
        finally:
            staging.unlink(missing_ok=True)
        return _sha256(data)

    def snapshot_dataset(self, run_id: str, dataset_path: str | Path) -> dict[str, str]:
        src = Path(dataset_path)
        data = src.read_bytes()
        dst = self._run_dir(run_id) / "phase1" / "dataset.json"
        digest = self._atomic_write(dst, data)
        return {"path": str(dst.resolve()), "sha256": digest}

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return value
        raise TypeError("Phase 2 output must be a Pydantic model or dict")

    def write_phase2_batch(self, run_id: str, batch: Any) -> dict[str, Any]:
        payload = self._jsonable(batch)
        encoded = (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        phase2_dir = self._run_dir(run_id) / "phase2"
        output = phase2_dir / "phase2_enriched.json"
        digest = self._atomic_write(output, encoded)
        incidents = payload.get("incidents") or []
        manifest = {
            "schema_version": "1.0",
            "run_id": run_id,
            "output": output.name,
            "sha256": digest,
            "result_count": len(incidents),
        }
        self._atomic_write(
            phase2_dir / "manifest.json",
            (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        return {"path": str(output.resolve()), "sha256": digest, "result_count": len(incidents)}

    def _get_writer(self) -> Callable[[str | Path, str | Path], list[Path]]:
        if self.phase3_writer is not None:
            return self.phase3_writer
        from llm_prompt_generator import write_phase3_payloads
        return write_phase3_payloads

    def write_phase3_payloads(self, run_id: str, phase2_output: str | Path) -> dict[str, Any]:
        output_dir = self._run_dir(run_id) / "phase3_ready"
        manifest_path = output_dir / "manifest.json"
        if manifest_path.exists():
            return self._validate_phase3_manifest(manifest_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        self._get_writer()(phase2_output, output_dir)
        return self._validate_phase3_manifest(manifest_path)

    @staticmethod
    def _validate_phase3_manifest(manifest_path: Path) -> dict[str, Any]:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        entries = raw.get("payloads")
        if entries is None:
            entries = raw.get("entries", [])
        if not isinstance(entries, list):
            raise ValueError("Phase 3 manifest payload list is invalid")
        for entry in entries:
            rel = entry.get("path")
            expected = entry.get("sha256")
            if not rel or not expected:
                raise ValueError("Phase 3 manifest entry lacks path or sha256")
            candidate = (manifest_path.parent / rel).resolve()
            if candidate.parent != manifest_path.parent.resolve():
                raise ValueError("Phase 3 manifest path escapes output directory")
            if _sha256(candidate.read_bytes()) != expected:
                raise ValueError(f"Phase 3 artifact checksum mismatch: {rel}")
        declared = raw.get("payload_count", len(entries))
        if declared != len(entries):
            raise ValueError("Phase 3 manifest payload_count mismatch")
        return {"manifest_path": str(manifest_path.resolve()), "payload_count": len(entries)}

