"""FastAPI entrypoint for the laptop1 integration service.

Binds ONLY to 127.0.0.1 (configurable port, default 8102). Provides:

- POST /v1/notify/phase1-dataset  (called by the Phase 1 notifier)
- GET  /health
- GET  /status
- POST /v1/trigger                (manual trigger)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from . import config
from .artifact_manager import ArtifactManager
from .config import (
    BIND_HOST,
    BIND_PORT,
    CANONICAL_DATASET_FILE,
    HEALTH_PATH,
    NOTIFY_PATH,
    RECOVERY_ON_STARTUP,
    STATUS_PATH,
    TRIGGER_PATH,
    ensure_runtime_dirs,
)
from .controller import IntegrationController
from .health import HealthService
from .models import NotificationPayload, PipelineStatus, TriggerRequest, TriggerResponse
from .phase2_runtime import Phase2Runtime, Phase2RuntimeConfig
from .recovery import RecoveryManager
from .state_store import StateStore

logger = logging.getLogger("laptop1.integration")


class IntegrationService:
    """Composition root: wires runtime, store, controller, recovery, API."""

    def __init__(self) -> None:
        ensure_runtime_dirs()
        self.runtime = Phase2Runtime()
        self.store = StateStore(config.STATE_DB_PATH)
        self.controller = IntegrationController(self.runtime, self.store)
        self.artifacts = ArtifactManager()
        self.health = HealthService(self.store, self.runtime)
        self._lock = threading.Lock()
        self._last_notify_ts: float | None = None

        self.app = FastAPI(title="laptop1-phase1-phase2-integration", version="1.0.0")
        self._register_routes()
        self._notify_debounce: dict[str, float] = {}

    def _register_routes(self) -> None:
        app = self.app

        @app.post(NOTIFY_PATH)
        async def notify(payload: NotificationPayload) -> JSONResponse:
            now = time.time()
            key = payload.dataset_path
            last = self._notify_debounce.get(key, 0.0)
            if now - last < 5.0:
                # Debounce duplicate notifications for the same dataset path.
                return JSONResponse({"accepted": True, "deduplicated": True})
            self._notify_debounce[key] = now
            self._trigger_now()
            return JSONResponse({"accepted": True, "run_id": None})

        @app.get(HEALTH_PATH)
        async def health() -> PipelineStatus:
            return self.health.status()

        @app.get(STATUS_PATH)
        async def status() -> dict:
            last = self.store.get_last_run()
            counts = self.store.count_runs_by_status()
            return {
                "service": "laptop1-phase1-phase2-integration",
                "phase2_ready": self.runtime.ready,
                "last_run_id": last.run_id if last else None,
                "last_run_status": last.status.value if last else None,
                "run_status_counts": counts,
            }

        @app.post(TRIGGER_PATH)
        async def trigger(req: TriggerRequest) -> TriggerResponse:
            if not self.runtime.ready:
                # First call warms the runtime (embedder + chromadb) — heavy.
                try:
                    await self._warm_async()
                except Exception as exc:
                    raise HTTPException(status_code=503, detail=f"Phase2 warm failed: {exc}")
            try:
                run = self.controller.run_once(
                    req.dataset_path or CANONICAL_DATASET_FILE,
                )
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            return TriggerResponse(accepted=True, run_id=run.run_id, detail="ok")

    async def _warm_async(self) -> None:
        # Run warm in a thread to avoid blocking the event loop.
        import asyncio

        await asyncio.to_thread(self.runtime.warm)

    def _trigger_now(self) -> None:
        def _run() -> None:
            with self._lock:
                try:
                    self.runtime.warm()  # idempotent
                    self.controller.run_once(CANONICAL_DATASET_FILE)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Background run failed: %s", exc)

        threading.Thread(target=_run, daemon=True).start()

    def startup(self) -> None:
        """Called from uvicorn lifespan: warm runtime + recover stale runs."""
        logger.info("Warming Phase 2 runtime (embedder + chromadb)...")
        try:
            self.runtime.warm()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Phase 2 warm failed at startup: %s", exc)
        if RECOVERY_ON_STARTUP:
            try:
                recovery = RecoveryManager(self.controller, self.store, CANONICAL_DATASET_FILE)
                n = recovery.recover()
                logger.info("Recovery processed %d incomplete run(s)", n)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Recovery skipped: %s", exc)

    def shutdown(self) -> None:
        self.runtime.close()
        self.store.close()


_service: IntegrationService | None = None


def create_app() -> FastAPI:
    global _service
    if _service is None:
        _service = IntegrationService()
    return _service.app


def get_service() -> IntegrationService:
    if _service is None:
        _service = IntegrationService()
    return _service


def main() -> None:
    import uvicorn

    ensure_runtime_dirs()
    svc = get_service()
    svc.startup()
    uvicorn.run(
        svc.app,
        host=BIND_HOST,
        port=BIND_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
