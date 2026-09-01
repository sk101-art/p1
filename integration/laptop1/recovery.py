"""Crash recovery for incomplete Laptop 1 runs."""
from __future__ import annotations

import logging
from pathlib import Path

from .controller import IntegrationController
from .models import RunStatus
from .state_store import StateStore

logger = logging.getLogger("laptop1.integration.recovery")


class RecoveryManager:
    def __init__(self, controller: IntegrationController, store: StateStore,
                 dataset_path: str | Path) -> None:
        self.controller = controller
        self.store = store
        self.dataset_path = Path(dataset_path)

    def recover(self) -> int:
        recovered = 0
        for run in self.store.get_pending_runs():
            snapshot = Path(run.phase1_artifact_path) if run.phase1_artifact_path else None
            source = snapshot if snapshot and snapshot.is_file() else self.dataset_path
            if not source.exists() and not (
                    run.phase2_output_path and Path(run.phase2_output_path).is_file()):
                logger.error("Cannot recover %s: source dataset and Phase 2 artifact missing",
                             run.run_id)
                continue
            record = self.controller.run_once(source, run_id=run.run_id)
            if record.status in (RunStatus.PHASE3_INPUTS_READY, RunStatus.SUCCEEDED):
                recovered += 1
            else:
                logger.error("Recovery failed for %s: %s", run.run_id, record.error)
        return recovered


__all__ = ["RecoveryManager"]
