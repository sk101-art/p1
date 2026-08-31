"""Tests for the health probe (no heavy Phase 2 runtime required)."""
from __future__ import annotations

from pathlib import Path

from integration.laptop1.health import HealthService
from integration.laptop1.models import IntegrationRunRecord, PipelineStatus, RunStatus
from integration.laptop1.state_store import StateStore


class _StubRuntime:
    """Minimal stand-in exposing only the ``ready`` property."""

    def __init__(self, ready: bool = False) -> None:
        self._ready = ready

    @property
    def ready(self) -> bool:
        return self._ready


def test_health_initial_state(state_db_path: Path):
    store = StateStore(state_db_path)
    svc = HealthService(store, _StubRuntime(ready=False))
    status = svc.status()

    assert isinstance(status, PipelineStatus)
    # No runs yet, nothing pending -> healthy.
    assert status.healthy is True
    assert status.last_run_id is None
    assert status.last_run_status is None
    assert status.phase2_ready is False
    assert status.pending_runs == 0
    assert status.uptime_seconds >= 0
    store.close()


def test_health_reports_last_run(state_db_path: Path):
    store = StateStore(state_db_path)
    store.create_run(
        IntegrationRunRecord(
            run_id="run_h1",
            dataset_generated_at="2026-08-18T02:53:37Z",
            dataset_incident_count=3,
            status=RunStatus.SUCCEEDED,
            finished_at="2026-08-18T03:00:00Z",
        )
    )
    svc = HealthService(store, _StubRuntime(ready=True))
    status = svc.status()

    assert status.last_run_id == "run_h1"
    assert status.last_run_status == RunStatus.SUCCEEDED
    assert status.last_run_finished_at is not None
    assert status.phase2_ready is True
    assert status.healthy is True
    store.close()


def test_health_unhealthy_with_pending_and_runtime_down(state_db_path: Path):
    store = StateStore(state_db_path)
    store.create_run(
        IntegrationRunRecord(
            run_id="run_h2",
            dataset_generated_at="2026-08-18T02:53:37Z",
            dataset_incident_count=3,
            status=RunStatus.PENDING,
        )
    )
    svc = HealthService(store, _StubRuntime(ready=False))
    status = svc.status()

    # Pending work exists and Phase 2 is not ready -> unhealthy.
    assert status.pending_runs == 1
    assert status.phase2_ready is False
    assert status.healthy is False
    store.close()
