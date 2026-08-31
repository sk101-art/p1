"""Deterministic unit tests for the FastMCP supervised worker manager.

Uses lightweight, spawn-compatible fake workers (no real embedding model or
Chroma store). The real model/Chroma worker is exercised separately in
tests/test_mcp_worker_runtime.py (marked ``runtime``).
"""

from __future__ import annotations

import concurrent.futures
import time
import unittest
from types import SimpleNamespace

from fastmcp_server import (SupervisedWorkerManager, handle_worker_request)


# ---------------------------------------------------------------------------
# Spawn-compatible fake workers (top-level so multiprocessing can pickle them)
# ---------------------------------------------------------------------------


def healthy_fake_worker(request_queue, response_queue):
    """Signals READY, then answers health/echo requests with matching request IDs."""
    response_queue.put({"event": "ready"})
    while True:
        req = request_queue.get()
        if req is None or req == "STOP":
            break
        action = req.get("action")
        if action == "health":
            response_queue.put(
                {"request_id": req.get("request_id"), "data": {"status": "ready", "records": 0}}
            )
        elif action == "echo":
            response_queue.put(
                {
                    "request_id": req.get("request_id"),
                    "data": {"echo": req.get("params", {}).get("value")},
                }
            )
        else:
            response_queue.put(
                {"request_id": req.get("request_id"), "data": {"error": f"Unknown action: {action}"}}
            )


def hanging_fake_worker(request_queue, response_queue):
    """Signals READY, then genuinely blocks forever on the 'block' action."""
    response_queue.put({"event": "ready"})
    while True:
        req = request_queue.get()
        if req is None or req == "STOP":
            break
        if req.get("action") == "block":
            time.sleep(86400)  # genuine block: never responds
        else:
            response_queue.put(
                {"request_id": req.get("request_id"), "data": {"status": "ready", "records": 0}}
            )


def mismatched_response_worker(request_queue, response_queue):
    """Signals READY, then answers every request with a wrong request ID."""
    response_queue.put({"event": "ready"})
    while True:
        req = request_queue.get()
        if req is None or req == "STOP":
            break
        response_queue.put({"request_id": "wrong-id", "data": {"status": "ready"}})


def failing_init_worker(request_queue, response_queue):
    """Reports a structured initialization failure instead of READY."""
    response_queue.put(
        {"event": "init_error", "error": "WorkerInitError", "details": "model load failed"}
    )


# ---------------------------------------------------------------------------
# In-process unit tests for the pure request handler
# ---------------------------------------------------------------------------


class FakeMemory:
    collection_name = "fake_collection"

    @property
    def collection(self):
        return SimpleNamespace(count=lambda: 7)

    def find_exact(self, target_service, log_template):
        return []

    def search(self, error_log, **kwargs):
        return []


class TestHandleWorkerRequest(unittest.TestCase):
    """Unit-test the pure request handler directly in the parent process."""

    def test_health(self):
        res = handle_worker_request(FakeMemory(), {"action": "health", "params": {}})
        self.assertEqual(res["status"], "ready")
        self.assertEqual(res["collection"], "fake_collection")
        self.assertEqual(res["records"], 7)

    def test_unknown_action(self):
        res = handle_worker_request(FakeMemory(), {"action": "nope", "params": {}})
        self.assertIn("Unknown action", res["error"])

    def test_search_passes_params(self):
        captured = {}

        class SpyMemory(FakeMemory):
            def search(self, error_log, **kwargs):
                captured["error_log"] = error_log
                captured.update(kwargs)
                return []

        handle_worker_request(
            SpyMemory(),
            {
                "action": "search",
                "params": {
                    "error_log": "boom",
                    "target_service": "svc",
                    "top_k": 3,
                    "min_similarity": 0.9,
                },
            },
        )
        self.assertEqual(captured["error_log"], "boom")
        self.assertEqual(captured["target_service"], "svc")
        self.assertEqual(captured["top_k"], 3)
        self.assertEqual(captured["min_similarity"], 0.9)

    def test_find_exact_passes_params(self):
        captured = {}

        class SpyMemory(FakeMemory):
            def find_exact(self, target_service, log_template):
                captured["target_service"] = target_service
                captured["log_template"] = log_template
                return []

        handle_worker_request(
            SpyMemory(),
            {"action": "find_exact", "params": {"target_service": "svc", "log_template": "tpl"}},
        )
        self.assertEqual(captured["target_service"], "svc")
        self.assertEqual(captured["log_template"], "tpl")


# ---------------------------------------------------------------------------
# Manager behavior tests using fake workers
# ---------------------------------------------------------------------------


class TestSupervisedWorkerManager(unittest.TestCase):
    def setUp(self):
        self.manager = SupervisedWorkerManager(
            worker_target=healthy_fake_worker, startup_timeout=10.0
        )

    def tearDown(self):
        self.manager.stop_worker()

    def test_health_returns_ready(self):
        res = self.manager.execute_request("health", {}, timeout_seconds=15.0)
        self.assertEqual(res.get("status"), "ready")

    def test_concurrent_requests_serialized_with_matching_ids(self):
        def call(_):
            return self.manager.execute_request("health", {}, timeout_seconds=30.0)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(call, range(4)))
        self.assertEqual(len(results), 4)
        for res in results:
            self.assertEqual(res.get("status"), "ready")

    def test_timeout_terminates_and_restarts_worker(self):
        # Use a worker that genuinely blocks on the "block" action so the
        # per-request timeout actually fires (healthy_fake_worker would answer
        # instantly with "Unknown action: block" and never time out).
        manager = SupervisedWorkerManager(
            worker_target=hanging_fake_worker, startup_timeout=10.0
        )
        try:
            old_process = manager.process
            self.assertIsNotNone(old_process)
            old_pid = old_process.pid

            t0 = time.time()
            res = manager.execute_request("block", {}, timeout_seconds=1.0)
            t1 = time.time()

            # The parent returns within timeout + 1.5 seconds
            self.assertLess(t1 - t0, 2.5)
            self.assertIn("timed out", res.get("error", "").lower())

            # Old worker process is terminated/dead
            old_process.join(timeout=2.0)
            self.assertFalse(old_process.is_alive())

            # Timeout handling is lazy: does NOT wait for replacement initialization
            # manager.process must be None immediately after timeout
            self.assertIsNone(manager.process)

            # The next request creates a new PID and it succeeds
            res2 = manager.execute_request("health", {}, timeout_seconds=15.0)
            self.assertEqual(res2.get("status"), "ready")

            new_process = manager.process
            self.assertIsNotNone(new_process)
            self.assertNotEqual(old_pid, new_process.pid)
            self.assertTrue(new_process.is_alive())
        finally:
            manager.stop_worker()

    def test_stop_worker_leaves_no_live_child(self):
        manager = SupervisedWorkerManager(
            worker_target=healthy_fake_worker, startup_timeout=10.0
        )
        proc = manager.process
        self.assertIsNotNone(proc)
        self.assertTrue(proc.is_alive())
        manager.stop_worker()
        proc.join(timeout=2.0)
        self.assertFalse(proc.is_alive())
        self.assertIsNone(manager.process)


class TestWorkerFailureModes(unittest.TestCase):
    def test_mismatched_response_id_triggers_restart(self):
        manager = SupervisedWorkerManager(
            worker_target=mismatched_response_worker, startup_timeout=10.0
        )
        try:
            old_process = manager.process
            self.assertIsNotNone(old_process)
            res = manager.execute_request("health", {}, timeout_seconds=15.0)
            self.assertIn("RequestIDMismatch", res.get("error", ""))
            self.assertIsNone(manager.process)  # Lazy restart: reset to None
        finally:
            manager.stop_worker()

    def test_worker_init_error_raises_from_constructor(self):
        import fastmcp_server
        with self.assertRaises(fastmcp_server.WorkerInitError):
            SupervisedWorkerManager(worker_target=failing_init_worker, startup_timeout=10.0)

    def test_execute_request_returns_structured_init_error(self):
        manager = SupervisedWorkerManager(
            worker_target=healthy_fake_worker, startup_timeout=10.0
        )
        try:
            manager.stop_worker()
            manager.worker_target = failing_init_worker
            res = manager.execute_request("health", {}, timeout_seconds=15.0)
            self.assertEqual(res.get("error"), "WorkerInitError")
            self.assertIn("model load failed", res.get("details", ""))
        finally:
            manager.stop_worker()

    def test_missing_fastmcp_fails_at_import(self):
        # Verify that if fastmcp is missing, we raise an ImportError.
        # We can test by attempting to import fastmcp_server with a mock sys.modules.
        import sys
        import importlib
        from unittest.mock import patch
        with patch.dict(sys.modules, {'fastmcp': None, 'mcp': None, 'mcp.server.fastmcp': None}):
            with self.assertRaises(ImportError):
                import fastmcp_server
                importlib.reload(fastmcp_server)
        # Restore fastmcp_server state so other tests run with the original types
        import fastmcp_server
        importlib.reload(fastmcp_server)


class TestServerStartup(unittest.TestCase):
    def test_main_initializes_before_serving_and_always_shuts_down(self):
        """Production startup must establish readiness before mcp.run accepts traffic."""
        import fastmcp_server as server
        from unittest.mock import patch

        events = []
        with (
            patch.object(
                server,
                "get_worker_manager",
                side_effect=lambda: events.append("initialized"),
            ),
            patch.object(
                server.mcp,
                "run",
                side_effect=lambda **_kwargs: events.append("serving"),
            ),
            patch.object(
                server,
                "shutdown_worker",
                side_effect=lambda: events.append("shutdown"),
            ),
        ):
            server.main()

        self.assertEqual(events, ["initialized", "serving", "shutdown"])


if __name__ == "__main__":
    unittest.main()
