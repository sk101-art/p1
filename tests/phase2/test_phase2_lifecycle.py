"""Unit tests for Phase 2 memory lifecycle management (atomic directory swaps, backup manifests, OS locking, lease safety)."""

from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.phase2_test_support import FakeEmbedder
from phase2.locking import StoreLock, StoreLockError
from phase2.memory import IncidentMemory
from phase2_lifecycle import (backup_database, reset_database,
                              restore_database, verify_backup_manifest,
                              get_lease_path, is_service_active)


class TestPhase2Lifecycle(unittest.TestCase):
    def test_backup_and_manifest_verification(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db_dir = Path(d) / "db"
            backup_dir = Path(d) / "backup"

            memory = IncidentMemory(persist_dir=db_dir, embedder=FakeEmbedder())
            memory.index_dataset(
                {
                    "generated_at": "2026-08-30T10:00:00Z",
                    "incidents": [
                        {
                            "incident_event": {
                                "incident_id": "life_1",
                                "target_service": "test-service",
                                "severity": "HIGH",
                                "priority_score": 50.0,
                                "occurrence_count": 1,
                            },
                            "telemetry_evidence": {
                                "log_cluster_template": "Test error",
                                "log_samples": [],
                            },
                        }
                    ],
                }
            )

            b_path = backup_database(persist_dir=db_dir, dest_dir=backup_dir)
            self.assertIsNotNone(b_path)
            self.assertTrue(verify_backup_manifest(b_path))

            # Corrupt manifest hash and assert failure
            manifest_file = b_path / "manifest.json"
            manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
            for k in manifest_data["file_hashes"]:
                manifest_data["file_hashes"][k] = "badhash12345"
            manifest_file.write_text(json.dumps(manifest_data), encoding="utf-8")
            self.assertFalse(verify_backup_manifest(b_path))

    def test_atomic_tombstone_reset(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db_dir = Path(d) / "db"
            memory = IncidentMemory(persist_dir=db_dir, embedder=FakeEmbedder())
            memory.index_dataset(
                {
                    "generated_at": "2026-08-30T10:00:00Z",
                    "incidents": [
                        {
                            "incident_event": {
                                "incident_id": "life_reset_1",
                                "target_service": "test-service",
                                "severity": "HIGH",
                                "priority_score": 50.0,
                                "occurrence_count": 1,
                            },
                            "telemetry_evidence": {
                                "log_cluster_template": "Reset test error",
                                "log_samples": [],
                            },
                        }
                    ],
                }
            )
            self.assertEqual(memory.collection.count(), 1)
            memory.close()

            success = reset_database(persist_dir=db_dir, force=True)
            self.assertTrue(success)

            fresh_mem = IncidentMemory(persist_dir=db_dir, embedder=FakeEmbedder())
            self.assertEqual(fresh_mem.collection.count(), 0)

    def test_store_lock_exclusivity(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            lock_path = Path(d) / "test.lock"
            with StoreLock(lock_file=lock_path):
                self.assertTrue(lock_path.exists())
                # Attempt to acquire lock again with short timeout -> expect StoreLockError
                with self.assertRaises(StoreLockError):
                    lock2 = StoreLock(lock_file=lock_path, timeout=0.2)
                    lock2.acquire()

    def test_dry_run_flag(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db_dir = Path(d) / "db"
            db_dir.mkdir(parents=True, exist_ok=True)
            res = backup_database(persist_dir=db_dir, dry_run=True)
            self.assertIsNotNone(res)
            # Ensure no staged/actual backup directories were created
            backups = list(db_dir.parent.glob("backup_*"))
            self.assertEqual(len(backups), 0)

    def test_active_server_lease_rejection(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db_dir = Path(d) / "db"
            db_dir.mkdir(parents=True, exist_ok=True)
            lease_path = get_lease_path(db_dir)

            # Create an active lease file matching this host and current active process PID
            import os
            lease_data = {
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "started_at": "2026-08-30T10:00:00Z",
                "persist_dir": str(db_dir),
            }
            lease_path.write_text(json.dumps(lease_data), encoding="utf-8")

            # Check service status
            self.assertTrue(is_service_active(db_dir))

            # Backup, reset, and restore should refuse to execute
            self.assertIsNone(backup_database(persist_dir=db_dir))
            self.assertFalse(reset_database(persist_dir=db_dir, force=True))
            self.assertFalse(restore_database(Path(d) / "some_backup", persist_dir=db_dir))

    def test_mismatched_lease_directory_fails_closed(self):
        """A lease stored at this database's lease path must match that database."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db_dir = Path(d) / "db"
            db_dir.mkdir(parents=True, exist_ok=True)
            lease_path = get_lease_path(db_dir)

            import os
            lease_data = {
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "started_at": "2026-08-30T10:00:00Z",
                "persist_dir": str(Path(d) / "different-db"),
            }
            lease_path.write_text(json.dumps(lease_data), encoding="utf-8")

            self.assertTrue(is_service_active(db_dir))
            self.assertIsNone(backup_database(persist_dir=db_dir))

    def test_stale_local_lease_handling(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db_dir = Path(d) / "db"
            db_dir.mkdir(parents=True, exist_ok=True)
            lease_path = get_lease_path(db_dir)

            # Create a stale lease with an invalid PID (999999)
            lease_data = {
                "pid": 999999,
                "hostname": socket.gethostname(),
                "started_at": "2026-08-30T10:00:00Z",
                "persist_dir": str(db_dir),
            }
            lease_path.write_text(json.dumps(lease_data), encoding="utf-8")

            # Check service status
            self.assertFalse(is_service_active(db_dir))

    def test_backup_destination_collision(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db_dir = Path(d) / "db"
            backup_dir = Path(d) / "backup"
            backup_dir.mkdir(parents=True, exist_ok=True)

            # Try to backup to an already existing destination -> Refuse
            self.assertIsNone(backup_database(persist_dir=db_dir, dest_dir=backup_dir))

    def test_restore_rejects_missing_manifest_or_backup_file(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db_dir = Path(d) / "db"
            self.assertFalse(restore_database(Path(d) / "non_existent_backup", persist_dir=db_dir))

    def test_reset_rollback_on_count_mismatch(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db_dir = Path(d) / "db"
            
            # Setup original database
            memory = IncidentMemory(persist_dir=db_dir, embedder=FakeEmbedder())
            memory.index_dataset(
                {
                    "generated_at": "2026-08-30T10:00:00Z",
                    "incidents": [
                        {
                            "incident_event": {
                                "incident_id": "orig_reset",
                                "target_service": "test-service",
                                "severity": "HIGH",
                                "priority_score": 50.0,
                                "occurrence_count": 1,
                            },
                            "telemetry_evidence": {
                                "log_cluster_template": "Original error",
                                "log_samples": [],
                            },
                        }
                    ],
                }
            )
            memory.close()

            # Mock IncidentMemory to raise an error during fresh count check on reset
            with patch("phase2_lifecycle.IncidentMemory", side_effect=RuntimeError("simulated init error")):
                success = reset_database(persist_dir=db_dir, force=True)
                self.assertFalse(success)
                
                # Check original DB was rolled back
                original_mem = IncidentMemory(persist_dir=db_dir, embedder=FakeEmbedder())
                self.assertEqual(original_mem.collection.count(), 1)
                original_mem.close()

    def test_reset_rejected_without_force(self):
        """Destructive reset must be refused without --force confirmation."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db_dir = Path(d) / "db"
            db_dir.mkdir(parents=True, exist_ok=True)
            result = reset_database(persist_dir=db_dir, force=False)
            self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
