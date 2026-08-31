"""Verify frozen Phase 2 files exactly match the frozen manifest.

Usage:
    python integration/verify_frozen_phase2.py

Behavior:
1. Loads integration/frozen_phase2_manifest.json.
2. Enumerates every expected frozen file.
3. Computes Git-compatible blob SHA (sha1 of "blob <size>\\0<content>").
4. Compares every file against the manifest.
5. Detects changed files.
6. Detects missing files.
7. Detects unexpected files within explicitly frozen owned paths where applicable.
8. Prints every mismatch clearly.
9. Exits 0 only when all frozen files are valid.
10. Never modifies files.
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(ROOT, "integration", "frozen_phase2_manifest.json")

# Explicitly frozen owned directories (within which any unexpected file is a violation).
FROZEN_OWNED_PATHS = {
    "phase2": [
        "phase2/__init__.py",
        "phase2/config.py",
        "phase2/filtering.py",
        "phase2/locking.py",
        "phase2/memory.py",
        "phase2/models.py",
        "phase2/normalization.py",
        "phase2/phase3_contract.py",
        "phase2/pipeline.py",
        "phase2/schema.py",
    ],
    "tests/phase2": [
        "tests/phase2/test_masking_spy.py",
        "tests/phase2/test_mcp_timeout.py",
        "tests/phase2/test_mcp_worker_runtime.py",
        "tests/phase2/test_phase2_chroma_integration.py",
        "tests/phase2/test_phase2_contract.py",
        "tests/phase2/test_phase2_e2e.py",
        "tests/phase2/test_phase2_eval_metrics.py",
        "tests/phase2/test_phase2_filtering.py",
        "tests/phase2/test_phase2_lifecycle.py",
        "tests/phase2/test_phase2_memory.py",
        "tests/phase2/test_phase2_output_contract.py",
        "tests/phase2/test_phase2_pipeline.py",
        "tests/phase2/test_phase2_pipeline_main.py",
        "tests/phase2/test_phase2_schema.py",
        "tests/phase2/test_phase2_schema_main.py",
    ],
    "contracts": [
        "contracts/phase2_input_v1.schema.json",
        "contracts/phase2_result_v1.schema.json",
        "contracts/phase2_batch_output_v1.schema.json",
        "contracts/phase3_input_v1.schema.json",
    ],
}


def git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(header + data).hexdigest()


def main() -> int:
    if not os.path.exists(MANIFEST_PATH):
        print(f"ERROR: manifest not found: {MANIFEST_PATH}")
        return 1

    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    tag = manifest.get("tag")
    commit = manifest.get("commit")
    expected_files = manifest.get("files", [])
    print(f"Frozen Phase 2 manifest: tag={tag} commit={commit} files={len(expected_files)}")

    errors = []

    # 1. Verify manifest structure
    if tag != "phase2-v1.0.0-rc1":
        errors.append(f"manifest tag mismatch: {tag}")
    if commit != "ad9c5296a77f8a136daf06f496eaf2c6834214d5":
        errors.append(f"manifest commit mismatch: {commit}")

    expected_map = {}
    for entry in expected_files:
        path = entry.get("path")
        sha = entry.get("git_blob_sha")
        if not path or not sha or not isinstance(sha, str) or len(sha) != 40:
            errors.append(f"manifest entry invalid: {entry}")
            continue
        expected_map[path] = sha

    # 2. Detect missing files
    for path in expected_map:
        local = os.path.join(ROOT, path)
        if not os.path.exists(local):
            errors.append(f"MISSING: {path}")

    # 3. Detect changed files + verify blob SHA
    for path, expected_sha in expected_map.items():
        local = os.path.join(ROOT, path)
        if not os.path.exists(local):
            continue
        with open(local, "rb") as f:
            data = f.read()
        actual_sha = git_blob_sha(data)
        if actual_sha != expected_sha:
            errors.append(
                f"CHANGED: {path} expected {expected_sha} got {actual_sha}"
            )

    # 4. Detect unexpected files within frozen owned paths
    for owned_dir, owned_files in FROZEN_OWNED_PATHS.items():
        owned_set = set(owned_files)
        base = os.path.join(ROOT, owned_dir)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for fn in filenames:
                if fn.endswith((".pyc", ".pyo")) or fn == "__pycache__":
                    continue
                abs_path = os.path.join(dirpath, fn)
                rel = os.path.relpath(abs_path, ROOT).replace("\\", "/")
                if rel not in owned_set:
                    errors.append(f"UNEXPECTED IN FROZEN PATH: {rel}")

    # 5. Report
    if errors:
        print("\n=== FROZEN PHASE 2 VERIFICATION FAILED ===")
        for err in errors:
            print(f"  - {err}")
        print(f"\n{len(errors)} problem(s) found.")
        return 1

    print("\n=== FROZEN PHASE 2 VERIFICATION PASSED ===")
    print(f"All {len(expected_map)} frozen Phase 2-owned files are valid and byte-identical to the manifest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
