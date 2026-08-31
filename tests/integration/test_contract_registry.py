"""Tests for the frozen Phase 2 contract registry."""
from __future__ import annotations

from pathlib import Path

from integration.laptop1.contract_registry import ContractRegistry


def test_contract_check_passes_on_clean_tree():
    """The frozen manifest must validate against the current worktree."""
    reg = ContractRegistry()
    result = reg.check()
    assert result.ok is True, f"Contract violations: {[ (v.path, v.reason) for v in result.violations ]}"
    assert result.checked_files > 0
    assert result.tag == "phase2-v1.0.0-rc1"


def test_contract_detects_changed_file(tmp_path: Path):
    """If a frozen file is modified, the registry must flag CHANGED."""
    import json

    from integration.laptop1.config import REPO_ROOT

    reg = ContractRegistry()
    manifest = reg.load_manifest()
    assert manifest["files"], "manifest should contain frozen files"

    # Pick an existing frozen file to tamper with.
    src = None
    rel = None
    for f in manifest["files"]:
        p = REPO_ROOT / f["path"]
        if p.exists():
            src = p
            rel = f["path"]
            break
    assert src is not None, "no existing frozen file found"

    # Copy the file into the worktree (under runtime/, which is gitignored),
    # tamper it, and build a manifest referencing it via a repo-relative path.
    tampered_abs = REPO_ROOT / "runtime" / "tmp_contract_test" / "tampered.txt"
    tampered_abs.parent.mkdir(parents=True, exist_ok=True)
    tampered_abs.write_bytes(src.read_bytes() + b"\n# tampered\n")
    tampered_rel = tampered_abs.relative_to(REPO_ROOT).as_posix()

    new_manifest = dict(manifest)
    new_manifest["files"] = [
        {**f, "path": tampered_rel} if f["path"] == rel else f
        for f in manifest["files"]
    ]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(new_manifest), encoding="utf-8")

    reg2 = ContractRegistry(manifest_path=manifest_path)
    result = reg2.check()
    assert result.ok is False
    assert any(v.reason == "CHANGED" for v in result.violations)
