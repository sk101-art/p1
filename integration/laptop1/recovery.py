"""Crash recovery for the laptop1 integration service.

On startup (and optionally on a timer), the recovery routine inspects the
StateStore for runs left in PENDING or RUNNING state from a previous process
that died. Those runs are re-driven through the controller so no dataset is
silently dropped.

Recovery is idempotent: Phase 2 processing is deterministic per dataset, and
re-running a RUNNING run simply overwrites its durable record.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .controller import IntegrationController
from .models import RunStatus
from .state_store import StateStore

logger = logging.getLogger("laptop1.integration.recovery")


class RecoveryManager:
    def __init__(
        self,
        controller: IntegrationController,
        store: StateStore,
        dataset_path: str | Path,
    ) -> None:
        self.controller = controller
        self.store = store
        self.dataset_path = Path(dataset_path)

    def recover(self) -> int:
        """Re-drive any incomplete runs. Returns number of runs recovered."""
        pending = self.store.get_pending_runs()
        recovered = 0
        for run in pending:
            if not self.dataset_path.exists():
                logger.warning(
                    "Skipping recovery for %s: dataset missing at %s",
                    run.run_id,
                    self.dataset_path,
                )
                continue
            logger.info("Recovering run %s (status=%s)", run.run_id, run.status.value)
            try:
                self.controller.run_once(
                    self.dataset_path,
                    run_id=run.run_id,
                )
                recovered += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("Recovery failed for %s: %s", run.run_id, exc)
        return recovered


__all__ = ["RecoveryManager"]
