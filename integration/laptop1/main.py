"""Loopback-only FastAPI service for the complete Laptop 1 boundary."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import config
from .artifact_manager import ArtifactManager
from .contract_registry import ContractRegistry
from .controller import IntegrationController
from .health import HealthService
from .models import NotificationPayload, PipelineStatus, TriggerRequest, TriggerResponse
from .phase2_runtime import Phase2Runtime
from .recovery import RecoveryManager
from .state_store import StateStore

logger = logging.getLogger("laptop1.integration")


class IntegrationService:
    def __init__(self) -> None:
        config.ensure_runtime_dirs()
        self.runtime = Phase2Runtime()
        self.store = StateStore(config.STATE_DB_PATH)
        self.artifacts = ArtifactManager()
        self.controller = IntegrationController(self.runtime, self.store, self.artifacts)
        self.health = HealthService(self.store, self.runtime)
        self.contracts = ContractRegistry()
        self._run_lock = threading.Lock()
        self._notify_debounce: dict[str, tuple[float, str]] = {}
        self.app = FastAPI(title="laptop1-phase1-phase2-integration", version="1.1.0")
        self._register_routes()

    def _register_routes(self) -> None:
        @self.app.post(config.NOTIFY_PATH)
        async def notify(payload: NotificationPayload) -> JSONResponse:
            dataset = Path(payload.dataset_path).resolve()
            canonical = config.CANONICAL_DATASET_FILE.resolve()
            if str(dataset).casefold() != str(canonical).casefold():
                raise HTTPException(status_code=400, detail="notification dataset_path is not canonical")
            key = str(dataset).casefold()
            now = time.time()
            previous = self._notify_debounce.get(key)
            if previous and now - previous[0] < 5.0:
                return JSONResponse({"accepted": True, "deduplicated": True,
                                     "run_id": previous[1]})
            run_id = f"run_{uuid.uuid4().hex}"
            self._notify_debounce[key] = (now, run_id)
            self._trigger_now(dataset, run_id)
            return JSONResponse({"accepted": True, "deduplicated": False,
                                 "run_id": run_id})

        @self.app.get(config.HEALTH_PATH)
        async def health() -> PipelineStatus:
            return self.health.status()

        @self.app.get(config.HEALTHZ_PATH)
        async def healthz() -> dict[str, str]:
            return {"status": "alive"}

        @self.app.get(config.READYZ_PATH)
        async def readyz() -> JSONResponse:
            payload = self.readiness()
            return JSONResponse(payload, status_code=200 if payload["status"] == "ready" else 503)

        @self.app.get(config.STATUS_PATH)
        async def status() -> dict:
            last = self.store.get_last_run()
            return {
                "service": "laptop1-phase1-phase2-integration",
                "phase2_ready": self.runtime.ready,
                "last_run_id": last.run_id if last else None,
                "last_run_status": last.status.value if last else None,
                "run_status_counts": self.store.count_runs_by_status(),
            }

        @self.app.get(config.JOBS_PATH)
        async def job(run_id: str) -> dict:
            record = self.store.get_run(run_id)
            if record is None:
                raise HTTPException(status_code=404, detail="run not found")
            payload = record.model_dump(mode="json")
            payload["result_count"] = len(self.store.get_results_for_run(run_id))
            return payload

        @self.app.post(config.TRIGGER_PATH)
        async def trigger(request: TriggerRequest) -> TriggerResponse:
            if not self.runtime.ready:
                try:
                    await self._warm_async()
                except Exception as exc:
                    raise HTTPException(status_code=503, detail=f"Phase2 warm failed: {exc}")
            run = self.controller.run_once(
                request.dataset_path or config.CANONICAL_DATASET_FILE)
            if run.status.value == "FAILED":
                raise HTTPException(status_code=500, detail=run.error or "run failed")
            return TriggerResponse(accepted=True, run_id=run.run_id,
                                   detail=run.status.value)

    def readiness(self) -> dict[str, object]:
        try:
            contract = self.contracts.check()
        except Exception as exc:  # noqa: BLE001
            logger.error("Contract verification failed: %s", exc)
            contract = None
        frozen_ok = bool(contract and contract.ok)
        database_ok = self.store.ping()
        runtime_ok = self.runtime.ready
        ready = frozen_ok and database_ok and runtime_ok
        return {
            "status": "ready" if ready else "not_ready",
            "phase2_tag": contract.tag if contract else None,
            "phase2_commit": contract.commit if contract else None,
            "phase2_frozen_files_valid": frozen_ok,
            "contracts_valid": frozen_ok,
            "database_ready": database_ok,
            "chroma_ready": runtime_ok,
            "embedding_model_warm": runtime_ok,
            "queue_depth": len(self.store.get_pending_runs()),
        }

    async def _warm_async(self) -> None:
        import asyncio
        await asyncio.to_thread(self.runtime.warm)

    def _trigger_now(self, dataset_path: Path, run_id: str) -> None:
        def run() -> None:
            with self._run_lock:
                record = self.controller.run_once(dataset_path, run_id=run_id)
                if record.status.value == "FAILED":
                    logger.error("Background run %s failed: %s", run_id, record.error)
        threading.Thread(target=run, daemon=True, name=f"laptop1-{run_id}").start()

    def startup(self) -> None:
        contract = self.contracts.check()
        if not contract.ok:
            details = ", ".join(f"{item.path}:{item.reason}" for item in contract.violations)
            raise RuntimeError(f"Frozen Phase 2 contract verification failed: {details}")
        self.runtime.warm()
        if config.RECOVERY_ON_STARTUP:
            RecoveryManager(self.controller, self.store,
                            config.CANONICAL_DATASET_FILE).recover()

    def shutdown(self) -> None:
        self.runtime.close()
        self.store.close()


_service: IntegrationService | None = None


def create_app() -> FastAPI:
    return get_service().app


def get_service() -> IntegrationService:
    global _service
    if _service is None:
        _service = IntegrationService()
    return _service


def main() -> None:
    import uvicorn
    service = get_service()
    service.startup()
    uvicorn.run(service.app, host=config.BIND_HOST, port=config.BIND_PORT,
                log_level="info")


if __name__ == "__main__":
    main()
