"""Frozen Phase 2 contract registry.

Verifies that the files owned by the frozen Phase 2 release (tag
``phase2-v1.0.0-rc1``) remain byte-identical to the manifest generated from
Git. This enforces the "no modification of frozen Phase 2 internals" safety
invariant at runtime startup and in verification scripts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import REPO_ROOT

MANIFEST_PATH = REPO_ROOT / "integration" / "frozen_phase2_manifest.json"

# Paths owned by the integration bridge (never part of the frozen set).
FROZEN_OWNED_PATHS = ("phase2", "tests/phase2", "contracts")


@dataclass
class ContractViolation:
    path: str
    reason: str  # CHANGED | MISSING | UNEXPECTED


@dataclass
class ContractCheckResult:
    ok: bool
    violations: list[ContractViolation] = field(default_factory=list)
    checked_files: int = 0
    tag: str = ""
    commit: str = ""


def _git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(header + data).hexdigest()


class ContractRegistry:
    def __init__(self, manifest_path: Optional[Path] = None) -> None:
        self.manifest_path = manifest_path or MANIFEST_PATH

    def load_manifest(self) -> dict:
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def check(self) -> ContractCheckResult:
        manifest = self.load_manifest()
        tag = manifest.get("tag", "")
        commit = manifest.get("commit", "")
        frozen_files = {f["path"] for f in manifest.get("files", [])}

        result = ContractCheckResult(ok=True, tag=tag, commit=commit)
        seen: set[str] = set()

        for entry in manifest.get("files", []):
            rel = entry["path"]
            seen.add(rel)
            abs_path = REPO_ROOT / rel
            if not abs_path.exists():
                result.violations.append(ContractViolation(rel, "MISSING"))
                result.ok = False
                continue
            try:
                data = abs_path.read_bytes()
            except OSError as exc:
                result.violations.append(ContractViolation(rel, f"MISSING({exc})"))
                result.ok = False
                continue
            actual = _git_blob_sha(data)
            if actual != entry.get("git_blob_sha"):
                result.violations.append(ContractViolation(rel, "CHANGED"))
                result.ok = False
            else:
                result.checked_files += 1

        # Unexpected files inside frozen-owned paths are also violations.
        # Exclude __pycache__ directories and compiled bytecode (matching
        # verify_frozen_phase2.py behaviour).
        for base in FROZEN_OWNED_PATHS:
            base_abs = REPO_ROOT / base
            if not base_abs.exists():
                continue
            for abs_path in base_abs.rglob("*"):
                if not abs_path.is_file():
                    continue
                if abs_path.suffix in (".pyc", ".pyo") or "__pycache__" in abs_path.parts:
                    continue
                rel = abs_path.relative_to(REPO_ROOT).as_posix()
                if rel not in seen and rel not in frozen_files:
                    result.violations.append(ContractViolation(rel, "UNEXPECTED"))
                    result.ok = False

        return result


__all__ = ["ContractRegistry", "ContractCheckResult", "ContractViolation"]
