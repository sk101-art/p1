"""Health probe for the laptop1 integration service."""

from __future__ import annotations

import time
from typing import Optional

from .models import PipelineStatus, RunStatus
from .phase2_runtime import Phase2Runtime
from .state_store import StateStore


class HealthService:
    def __init__(self, store: StateStore, runtime: Phase2Runtime) -> None:
        self.store = store
        self.runtime = runtime
        self.started_at = time.time()

    def status(self) -> PipelineStatus:
        last = self.store.get_last_run()
        pending = len(self.store.get_pending_runs())
        healthy = self.runtime.ready or pending == 0

        return PipelineStatus(
            healthy=healthy,
            last_run_id=last.run_id if last else None,
            last_run_status=last.status if last else None,
            last_run_finished_at=last.finished_at if last else None,
            phase2_ready=self.runtime.ready,
            pending_runs=pending,
            uptime_seconds=time.time() - self.started_at,
        )


__all__ = ["HealthService"]
