"""Operational lifecycle manager for Phase 2 incident memory with atomic directory swaps, maintenance mode, manifests, and OS locking."""

from __future__ import annotations

import argparse
import datetime
import gc
import hashlib
import json
import socket
import shutil
from pathlib import Path
import psutil

from typing import Any
from phase2.config import BACKUP_DIR, DEFAULT_CHROMA_DIR
from phase2.locking import StoreLock
from phase2.memory import IncidentMemory


def _hash_file(filepath: Path) -> str:
    """Compute SHA-256 hash of a file."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def _compute_directory_manifest(directory: Path) -> dict[str, str]:
    """Compute relative filepath to SHA-256 hash dictionary for directory."""
    manifest = {}
    if directory.exists():
        for path in sorted(directory.rglob("*")):
            if path.is_file() and not path.name.endswith(".lock") and not path.name.startswith("."):
                rel_path = str(path.relative_to(directory)).replace("\\", "/")
                manifest[rel_path] = _hash_file(path)
    return manifest


def get_lease_path(persist_dir: Path) -> Path:
    """Get sibling lease file path derived from persist_dir."""
    p = Path(persist_dir).resolve()
    return p.parent / f".{p.name}_lease.json"


def is_service_active(persist_dir: Path) -> bool:
    """Check if the Phase 2 service is active. Fails closed on error/malformed lease."""
    lease_path = get_lease_path(persist_dir)
    if not lease_path.exists():
        return False
    try:
        data = json.loads(lease_path.read_text(encoding="utf-8"))
        lease_hostname = data.get("hostname")
        lease_pid = data.get("pid")
        lease_dir = data.get("persist_dir")

        # Validate types
        if not isinstance(lease_pid, int):
            return True
        if not isinstance(lease_hostname, str) or not lease_hostname:
            return True
        if not isinstance(lease_dir, str) or not lease_dir:
            return True

        # Validate lease persist_dir matches requested persist_dir
        try:
            if Path(lease_dir).resolve() != Path(persist_dir).resolve():
                # This lease path is derived from persist_dir, so different
                # contents indicate corruption or tampering. Fail closed.
                return True
        except Exception:
            # If resolution fails, fail closed
            return True

        if lease_hostname == socket.gethostname():
            if psutil.pid_exists(lease_pid):
                return True
            else:
                # dead local PID is stale
                return False
        else:
            # Different host: assume active to never silently override an active service
            return True
    except Exception:
        # Fail closed on any read/parse error
        return True


def backup_database(
    persist_dir: str | Path | None = None,
    dest_dir: str | Path | None = None,
    dry_run: bool = False,
) -> Path | None:
    """Create a quiesced external backup with SHA-256 file hashes and metadata manifest."""
    source = Path(persist_dir or DEFAULT_CHROMA_DIR).resolve()
    if is_service_active(source):
        print("Error: The Phase 2 MCP service is currently active. Stop the service first.")
        return None

    if not source.exists():
        print(f"Error: Source directory {source} does not exist.")
        return None

    # Resolve backup destination path (defaulting to BACKUP_DIR / backup_YYYYMMDD_HHMMSS_ffffff)
    b_dir = Path(dest_dir or BACKUP_DIR).resolve()
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    target = b_dir / f"backup_{timestamp}"

    # Containment check: prevent backup directory from being placed inside the live Chroma directory
    try:
        b_dir.relative_to(source)
        print(f"Error: Backup destination {b_dir} is inside the live database directory {source}. This would cause recursive copy/mutation.")
        return None
    except ValueError:
        pass  # Good: b_dir is not inside source

    if target.exists():
        print(f"Error: Invalid or existing backup destination: {target}")
        return None
    # Ensure the destination parent exists (e.g. artifacts/ may be absent in CI).
    b_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        print(f"[DRY-RUN] Would create external backup from {source} to {target}")
        return target

    print(f"Acquiring lock for backup on {source}...")
    with StoreLock(persist_dir=source, timeout=5.0):
        # Recheck lease after acquiring store lock
        if is_service_active(source):
            print("Error: The Phase 2 MCP service is active (detected after lock acquisition).")
            return None

        # Open store to extract record counts and collection details
        memory = IncidentMemory(persist_dir=source)
        record_count = int(memory.collection.count())
        col_name = memory.collection_name
        memory.close()
        gc.collect()

        import uuid
        staged_target = target.parent / f"{target.name}_staged_{uuid.uuid4().hex}"
        if staged_target.exists():
            raise FileExistsError(f"Staging collision detected for {staged_target}")
        staged_target.mkdir(parents=True, exist_ok=True)

        # Copy database files to staged directory
        for item in source.iterdir():
            if item.name == ".store.lock" or item.name.endswith(".lock") or item.name.startswith("."):
                continue
            dest_item = staged_target / item.name
            if item.is_dir():
                shutil.copytree(item, dest_item, ignore=shutil.ignore_patterns(".store.lock", "*.lock"))
            else:
                shutil.copy2(item, dest_item)

        # Generate manifest
        manifest_data: dict[str, Any] = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "collection_name": col_name,
            "record_count": record_count,
            "file_hashes": _compute_directory_manifest(staged_target),
        }
        manifest_path = staged_target / "manifest.json"
        manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

        # Verify staged hashes before final swap
        for rel_path, expected_hash in manifest_data["file_hashes"].items():
            copied_file = staged_target / rel_path
            if not copied_file.exists() or _hash_file(copied_file) != expected_hash:
                print("Error: Backup staging hash verification failed.")
                shutil.rmtree(staged_target)
                return None

        # Atomic rename to target
        staged_target.rename(target)
        print(f"Backup complete. Manifest saved to {target / 'manifest.json'}")
        return target


def verify_backup_manifest(backup_path: Path) -> bool:
    """Verify SHA-256 hashes in backup manifest."""
    manifest_path = backup_path / "manifest.json"
    if not manifest_path.exists():
        print(f"Backup verification failed: Missing {manifest_path}")
        return False
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        file_hashes = data.get("file_hashes", {})
        for rel_path, expected_hash in file_hashes.items():
            file_path = backup_path / rel_path
            if not file_path.exists():
                print(f"Verification error: Missing file {file_path}")
                return False
            actual_hash = _hash_file(file_path)
            if actual_hash != expected_hash:
                print(f"Verification error: Hash mismatch for {rel_path}")
                return False
        return True
    except Exception:
        return False


def restore_database(
    backup_path: str | Path,
    persist_dir: str | Path | None = None,
    dry_run: bool = False,
) -> bool:
    """Restore database via staged directory verification and atomic directory swap with rollback safety."""
    b_path = Path(backup_path).resolve()
    target = Path(persist_dir or DEFAULT_CHROMA_DIR).resolve()

    if not b_path.exists():
        print(f"Restore aborted: Backup path {b_path} does not exist.")
        return False

    if not verify_backup_manifest(b_path):
        print("Restore aborted: Backup manifest verification failed.")
        return False

    manifest_path = b_path / "manifest.json"
    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_count = manifest_data.get("record_count")
    expected_col = manifest_data.get("collection_name")

    if is_service_active(target):
        print("Error: The Phase 2 MCP service is currently active. Stop the service first.")
        return False

    if dry_run:
        print(f"[DRY-RUN] Would perform staged restore from {b_path} into {target}")
        return True

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    
    with StoreLock(persist_dir=target, timeout=5.0):
        if is_service_active(target):
            print("Error: The Phase 2 MCP service is active (detected after lock acquisition).")
            return False

        import uuid
        staged = target.parent / f"{target.name}_staged_{timestamp}_{uuid.uuid4().hex}"
        if staged.exists():
            raise FileExistsError(f"Staging collision detected for {staged}")
        staged.mkdir(parents=True, exist_ok=True)

        for item in b_path.iterdir():
            if item.name == "manifest.json" or item.name == ".store.lock" or item.name.endswith(".lock") or item.name.startswith("."):
                continue
            dest_item = staged / item.name
            if item.is_dir():
                shutil.copytree(item, dest_item, ignore=shutil.ignore_patterns(".store.lock", "*.lock"))
            else:
                shutil.copy2(item, dest_item)

        # Verify staged hashes
        for rel_path, expected_hash in manifest_data["file_hashes"].items():
            staged_file = staged / rel_path
            if not staged_file.exists() or _hash_file(staged_file) != expected_hash:
                print("Error: staged hash verification failed during restore.")
                shutil.rmtree(staged)
                return False

        # Open staged database and verify contents
        try:
            staged_mem = IncidentMemory(persist_dir=staged)
            staged_count = int(staged_mem.collection.count())
            staged_col = staged_mem.collection_name
            staged_mem.close()
            gc.collect()

            if staged_count != expected_count or staged_col != expected_col:
                raise RuntimeError(f"Staged verification failed: count={staged_count} (expected {expected_count}), col={staged_col} (expected {expected_col})")
        except Exception as exc:
            print(f"Staged DB verification failed: {exc}")
            shutil.rmtree(staged)
            return False

        has_original = target.exists()
        tombstone = target.parent / f"{target.name}_tombstone_{timestamp}"
        if has_original:
            try:
                target.rename(tombstone)
            except Exception as e:
                print(f"Error renaming live directory to tombstone: {e}")
                shutil.rmtree(staged)
                return False

        try:
            staged.rename(target)
        except Exception as e:
            print(f"Error renaming staged to live: {e}")
            if has_original and tombstone.exists():
                tombstone.rename(target)
            shutil.rmtree(staged)
            return False

        # Verify final live store
        try:
            restored_mem = IncidentMemory(persist_dir=target)
            restored_count = int(restored_mem.collection.count())
            restored_col = restored_mem.collection_name
            restored_mem.close()
            gc.collect()

            if restored_count != expected_count or restored_col != expected_col:
                raise RuntimeError("Verification of final live store failed.")

            print(f"Restore verification successful. Live database active with {restored_count} records.")
            return True
        except Exception as exc:
            print(f"Verification of final live store failed: {exc}. Rolling back...")
            if target.exists():
                shutil.rmtree(target)
            if has_original and tombstone.exists():
                tombstone.rename(target)
            return False


def reset_database(
    persist_dir: str | Path | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """Atomic tombstone reset: move live dir to tombstone, instantiate new live dir, verify 0 count, rollback on failure."""
    directory = Path(persist_dir or DEFAULT_CHROMA_DIR).resolve()
    if is_service_active(directory):
        print("Error: The Phase 2 MCP service is currently active. Stop the service first.")
        return False

    if not force:
        print("ERROR: Atomic reset requires --force flag or confirmation.")
        return False

    if dry_run:
        print(f"[DRY-RUN] Would perform atomic directory rename reset on {directory}")
        return True

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    tombstone = directory.parent / f"{directory.name}_tombstone_{timestamp}"

    # Acquire lock and hold it through the complete reset
    with StoreLock(persist_dir=directory, timeout=5.0):
        if is_service_active(directory):
            print("Error: The Phase 2 MCP service is active (detected after lock acquisition).")
            return False
        gc.collect()

        has_original = directory.exists()
        if has_original:
            try:
                directory.rename(tombstone)
            except Exception as e:
                print(f"Error renaming live directory to tombstone: {e}")
                return False

        directory.mkdir(parents=True, exist_ok=True)

        try:
            fresh_mem = IncidentMemory(persist_dir=directory)
            fresh_count = int(fresh_mem.collection.count())
            fresh_mem.close()
            gc.collect()

            if fresh_count != 0:
                raise RuntimeError(f"Reset verification failed: fresh database count is {fresh_count}")

            print(f"Atomic reset succeeded. Tombstone preserved at {tombstone}")
            return True

        except Exception as exc:
            print(f"Reset failed: {exc}. Rolling back tombstone to live...")
            if directory.exists():
                shutil.rmtree(directory)
            if has_original and tombstone.exists():
                tombstone.rename(directory)
            return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage Phase 2 Memory Store Lifecycle")
    parser.add_argument("--reset", action="store_true", help="Atomic tombstone reset")
    parser.add_argument("--backup", action="store_true", help="Create external backup")
    parser.add_argument("--restore", type=str, default=None, help="Restore from backup path")
    parser.add_argument("--dir", type=str, default=None, help="Chroma DB path")
    parser.add_argument("--dry-run", action="store_true", help="Preview lifecycle action")
    parser.add_argument("--force", action="store_true", help="Confirm destructive actions")
    args = parser.parse_args()

    if args.backup:
        backup_database(persist_dir=args.dir, dry_run=args.dry_run)
    elif args.restore:
        restore_database(args.restore, persist_dir=args.dir, dry_run=args.dry_run)
    elif args.reset:
        reset_database(persist_dir=args.dir, dry_run=args.dry_run, force=args.force)
    else:
        memory = IncidentMemory(persist_dir=args.dir)
        print(f"Collection: {memory.collection_name}")
        print(f"Incident Count: {memory.collection.count()}")


if __name__ == "__main__":
    main()
