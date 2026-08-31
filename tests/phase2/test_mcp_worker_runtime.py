"""Runtime test for the REAL supervised worker (SentenceTransformer + Chroma).

Marked ``runtime`` so the deterministic/coverage CI job excludes it:

    pytest -m "not runtime" --cov=phase2 --cov-fail-under=90
"""
from __future__ import annotations

import pytest

from fastmcp_server import SupervisedWorkerManager


@pytest.mark.runtime
class TestRealWorkerRuntime:
    def test_real_worker_ready_and_survives_restart(self):
        manager = SupervisedWorkerManager(startup_timeout=120.0)
        try:
            # Worker must signal READY within the startup window (model load
            # is bounded by startup_timeout, not the per-request timeout).
            res = manager.execute_request("health", {}, timeout_seconds=15.0)
            assert res.get("status") == "ready"
            assert isinstance(res.get("records"), int)

            old_pid = manager.process.pid if manager.process else None
            manager.restart_worker()
            assert manager.process is None

            res2 = manager.execute_request("health", {}, timeout_seconds=15.0)
            assert res2.get("status") == "ready"
            assert manager.process is not None
            assert manager.process.pid != old_pid
        finally:
            manager.stop_worker()
