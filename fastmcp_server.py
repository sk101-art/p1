"""Read-only FastMCP interface for Phase 2 incident memory with long-lived supervised worker process isolation."""

from __future__ import annotations

import atexit
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import threading
import traceback
import uuid
from typing import Any, Callable

# Fail fast on missing runtime dependencies
try:
    from fastmcp import FastMCP
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP  # type: ignore
    except ImportError as err:
        raise ImportError(
            "FastMCP dependency missing. Install requirements-phase2.txt to run Phase 2 server."
        ) from err

from phase2.config import (DEFAULT_TIMEOUT_SECONDS,
                           PROVISIONAL_MIN_SIMILARITY)

mcp = FastMCP("Auto-SRE Phase 2 Memory")


class WorkerInitError(RuntimeError):
    """Raised when the supervised worker process fails to initialize (model/Chroma load failure)."""


def handle_worker_request(memory: Any, request: dict[str, Any]) -> dict[str, Any]:
    """Handle a single request against an initialized memory store.

    Pure function with no IPC so it can be unit-tested directly in the parent
    process; the subprocess only manages IPC and lifecycle.
    """
    action = request.get("action")
    params = request.get("params") or {}

    if action == "find_exact":
        matches = memory.find_exact(
            params.get("target_service", ""),
            params.get("log_template", ""),
        )
        return {"matches": [m.model_dump(mode="json") for m in matches]}
    if action == "search":
        matches = memory.search(
            params.get("error_log", ""),
            target_service=params.get("target_service"),
            top_k=params.get("top_k", 5),
            min_similarity=params.get("min_similarity", PROVISIONAL_MIN_SIMILARITY),
        )
        return {"matches": [m.model_dump(mode="json") for m in matches]}
    if action == "health":
        return {
            "status": "ready",
            "collection": memory.collection_name,
            "records": int(memory.collection.count()),
        }
    return {"error": f"Unknown action: {action}"}


def _worker_loop(request_queue: mp.Queue, response_queue: mp.Queue) -> None:
    """Worker process loop: initialize memory once, signal READY, then handle requests sequentially."""
    try:
        from phase2.memory import IncidentMemory

        memory = IncidentMemory()
        memory._embed(["warmup query"])
    except Exception as init_exc:
        print(f"WORKER_INIT_ERROR: {init_exc}\n{traceback.format_exc()}", flush=True)
        response_queue.put(
            {
                "event": "init_error",
                "error": "WorkerInitError",
                "details": str(init_exc),
            }
        )
        return

    # Signal successful initialization so the manager starts the per-request
    # timeout clock only after the (potentially slow) model load completes.
    response_queue.put({"event": "ready"})

    while True:
        try:
            req = request_queue.get()
            if req is None or req == "STOP":
                break

            res_data = handle_worker_request(memory, req)
            response_queue.put({"request_id": req.get("request_id"), "data": res_data})
        except Exception as exc:
            response_queue.put(
                {
                    "request_id": req.get("request_id") if isinstance(req, dict) else None,
                    "error": type(exc).__name__,
                    "details": str(exc),
                }
            )


class SupervisedWorkerManager:
    """Manages a single long-lived supervised worker process with IPC queues, serialized caller access, and timeout termination.

    Startup and request deadlines are decoupled: the worker signals ``{"event": "ready"}``
    once initialization completes (or ``{"event": "init_error", ...}`` on failure). The
    manager waits up to ``startup_timeout`` seconds for that signal; only afterwards does
    the per-request ``timeout_seconds`` deadline apply.
    """

    def __init__(
        self,
        worker_target: Callable[[mp.Queue, mp.Queue], None] = _worker_loop,
        startup_timeout: float = 120.0,
    ) -> None:
        import socket
        import datetime
        from phase2.config import DEFAULT_CHROMA_DIR
        self.worker_target = worker_target
        self.startup_timeout = startup_timeout
        self.ctx = mp.get_context("spawn")
        self.request_queue: mp.Queue = self.ctx.Queue(maxsize=50)
        self.response_queue: mp.Queue = self.ctx.Queue(maxsize=50)
        self.process: Any = None
        self._ready = False
        self._execute_lock = threading.Lock()
        self.persist_dir = Path(DEFAULT_CHROMA_DIR).resolve()
        self.lease_path = self.persist_dir.parent / f".{self.persist_dir.name}_lease.json"
        
        lease_data = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "persist_dir": str(self.persist_dir),
        }
        try:
            self.persist_dir.parent.mkdir(parents=True, exist_ok=True)
            self.lease_path.write_text(json.dumps(lease_data, indent=2), encoding="utf-8")
        except Exception as exc:
            raise RuntimeError(f"Failed to create database lease file: {exc}") from exc

        with self._execute_lock:
            self._start_worker_locked()

    def _terminate_worker_locked(self) -> None:
        """Terminate the current worker process (if any) and wait for it to die."""
        if self.process and self.process.is_alive():
            try:
                self.request_queue.put_nowait("STOP")
            except Exception:
                pass
            self.process.terminate()
            self.process.join(timeout=0.5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=0.5)

    def _reset_ipc_locked(self) -> None:
        """Close queues, joining threads where supported, and recreate fresh queues."""
        try:
            self.request_queue.close()
            self.request_queue.join_thread()
        except Exception:
            pass
        try:
            self.response_queue.close()
            self.response_queue.join_thread()
        except Exception:
            pass
        self.request_queue = self.ctx.Queue(maxsize=50)
        self.response_queue = self.ctx.Queue(maxsize=50)

    def _mark_worker_stopped_locked(self) -> None:
        """Mark worker status as not ready and process as None."""
        self._ready = False
        self.process = None

    def _start_worker_locked(self) -> None:
        """Spawn the process and wait for READY event."""
        if self.process is not None and self.process.is_alive() and self._ready:
            return

        self._terminate_worker_locked()
        self.process = self.ctx.Process(
            target=self.worker_target,
            args=(self.request_queue, self.response_queue),
            daemon=True,
        )
        self.process.start() # type: ignore

        try:
            msg = self.response_queue.get(timeout=self.startup_timeout)
        except queue.Empty:
            self._terminate_worker_locked()
            self._mark_worker_stopped_locked()
            raise WorkerInitError(
                f"Worker failed to signal READY within {self.startup_timeout} seconds"
            ) from None

        if not isinstance(msg, dict) or msg.get("event") != "ready":
            self._terminate_worker_locked()
            self._mark_worker_stopped_locked()
            if isinstance(msg, dict) and msg.get("event") == "init_error":
                details = msg.get("details") or "worker initialization failed"
                raise WorkerInitError(details)
            raise WorkerInitError(f"Unexpected worker startup message: {msg!r}")

        self._ready = True

    def stop_worker(self) -> None:
        """Explicitly shut down worker process and clean up IPC queues to release Chroma storage descriptors."""
        with self._execute_lock:
            self._terminate_worker_locked()
            self._reset_ipc_locked()
            self._mark_worker_stopped_locked()
            try:
                if hasattr(self, "lease_path") and self.lease_path.exists():
                    self.lease_path.unlink()
            except Exception:
                pass

    def restart_worker(self) -> None:
        """Force terminate worker, discard queues, and mark as not ready.

        Does NOT immediately load the replacement model (lazy initialization on next request).
        """
        with self._execute_lock:
            self._terminate_worker_locked()
            self._reset_ipc_locked()
            self._mark_worker_stopped_locked()

    def execute_request(
        self, action: str, params: dict[str, Any], timeout_seconds: float = 15.0
    ) -> dict[str, Any]:
        with self._execute_lock:
            try:
                self._start_worker_locked()
            except WorkerInitError as exc:
                return {"error": "WorkerInitError", "details": str(exc), "matches": []}

            req_id = str(uuid.uuid4())
            payload = {"request_id": req_id, "action": action, "params": params}

            try:
                self.request_queue.put(payload, timeout=2.0)
            except queue.Full:
                return {"error": "RequestQueueFull", "matches": []}

            try:
                res = self.response_queue.get(timeout=timeout_seconds)
                if res.get("request_id") != req_id and "error" not in res:
                    # Request ID mismatch: stop immediately and mark for lazy restart
                    self._terminate_worker_locked()
                    self._reset_ipc_locked()
                    self._mark_worker_stopped_locked()
                    return {"error": "RequestIDMismatch", "matches": []}
                if "error" in res and res["error"] is not None and res["error"] != "ready":
                    return {"error": res["error"], "matches": []}
                return res.get("data") if isinstance(res, dict) and "data" in res else res
            except queue.Empty:
                # Timeout: terminate immediately, clear IPC, mark stopped, return immediately without loading replacement
                self._terminate_worker_locked()
                self._reset_ipc_locked()
                self._mark_worker_stopped_locked()
                return {
                    "error": f"Search timed out after {timeout_seconds} seconds",
                    "matches": [],
                }


_worker_manager: SupervisedWorkerManager | None = None
_worker_manager_lock = threading.Lock()


def get_worker_manager() -> SupervisedWorkerManager:
    """Get or create the worker manager (lazy initialization)."""
    global _worker_manager
    if _worker_manager is None:
        with _worker_manager_lock:
            if _worker_manager is None:
                _worker_manager = SupervisedWorkerManager()
    return _worker_manager


def shutdown_worker() -> None:
    """Maintenance mode: shut down worker process and close Chroma clients to allow lifecycle operations."""
    global _worker_manager
    with _worker_manager_lock:
        if _worker_manager is not None:
            _worker_manager.stop_worker()
            _worker_manager = None


atexit.register(shutdown_worker)


@mcp.tool()
def phase2_health() -> dict[str, Any]:
    """Return readiness and indexed-record count via worker process."""
    return get_worker_manager().execute_request("health", {}, timeout_seconds=15.0)


@mcp.tool()
def find_exact_incident(target_service: str, log_template: str) -> dict[str, Any]:
    """Find a deterministic exact match for a service and normalized template."""
    return get_worker_manager().execute_request(
        "find_exact",
        {"target_service": target_service, "log_template": log_template},
        timeout_seconds=10.0,
    )


@mcp.tool()
def search_historical_incidents(
    error_log: str,
    target_service: str | None = None,
    top_k: int = 5,
    min_similarity: float = PROVISIONAL_MIN_SIMILARITY,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Return bounded, thresholded semantic matches with supervised worker process isolation."""
    return get_worker_manager().execute_request(
        "search",
        {
            "error_log": error_log,
            "target_service": target_service,
            "top_k": top_k,
            "min_similarity": min_similarity,
        },
        timeout_seconds=timeout_seconds,
    )


def main() -> None:
    """Initialize Phase 2 completely before accepting MCP requests."""
    try:
        # Fail startup if the model, Chroma store, worker, or lease cannot initialize.
        get_worker_manager()
        mcp.run(
            transport=os.getenv("PHASE2_MCP_TRANSPORT", "http"),
            host=os.getenv("PHASE2_MCP_HOST", "127.0.0.1"),
            port=int(os.getenv("PHASE2_MCP_PORT", "8000")),
        )
    finally:
        shutdown_worker()


if __name__ == "__main__":
    main()
