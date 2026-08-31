"""Tests for the controller orchestration against the frozen Phase 2 runtime."""
from __future__ import annotations

from pathlib import Path

from integration.laptop1.controller import IntegrationController
from integration.laptop1.models import Phase2Outcome, RunStatus
from integration.laptop1.state_store import StateStore


def test_run_once_succeeds_and_persists(runtime, state_db_path: Path, synthetic_dataset_path: Path):
    store = StateStore(state_db_path)
    ctrl = IntegrationController(runtime, store)
    rec = ctrl.run_once(synthetic_dataset_path, run_id="run_ctrl_1")

    assert rec.status == RunStatus.SUCCEEDED
    assert rec.error is None
    # Every incident should be classified into an outcome bucket.
    assert rec.phase2_outcome_counts, "expected non-empty outcome counts"
    total = sum(rec.phase2_outcome_counts.values())
    assert total == 6  # 6 synthetic incidents

    # Results persisted.
    results = store.get_results_for_run("run_ctrl_1")
    assert len(results) == 6
    for r in results:
        assert r.outcome in (Phase2Outcome.ACTIONABLE, Phase2Outcome.NON_ACTIONABLE,
                             Phase2Outcome.QUARANTINED, Phase2Outcome.ERROR)
        assert r.incident_id is not None
    store.close()


def test_run_once_records_failure_on_bad_dataset(runtime, state_db_path: Path, tmp_path: Path):
    store = StateStore(state_db_path)
    ctrl = IntegrationController(runtime, store)
    bad = tmp_path / "bad.json"
    bad.write_text("not a dataset", encoding="utf-8")
    rec = ctrl.run_once(bad, run_id="run_bad")
    assert rec.status == RunStatus.FAILED
    assert rec.error is not None
    store.close()


def test_freshness_guard_rejects_stale_dataset(runtime, state_db_path: Path, tmp_path: Path):
    """A dataset older than the max age must be rejected (safety guard)."""
    import json
    from datetime import datetime, timedelta, timezone

    store = StateStore(state_db_path)
    ctrl = IntegrationController(runtime, store)
    old = tmp_path / "old.json"
    payload = {
        "generated_at": (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "metadata": {"git_sha": "deadbeef"},
        "incidents": [],
    }
    old.write_text(json.dumps(payload), encoding="utf-8")
    rec = ctrl.run_once(old, run_id="run_stale")
    assert rec.status == RunStatus.FAILED
    assert "stale" in (rec.error or "").lower()
    store.close()


def test_recovery_redrives_pending_run(runtime, state_db_path: Path, synthetic_dataset_path: Path):
    from integration.laptop1.recovery import RecoveryManager

    store = StateStore(state_db_path)
    # Seed a PENDING run that was never completed.
    from integration.laptop1.models import IntegrationRunRecord
    store.create_run(
        IntegrationRunRecord(
            run_id="run_pending",
            dataset_generated_at="2026-08-18T02:53:37Z",
            dataset_incident_count=6,
            status=RunStatus.PENDING,
        )
    )
    ctrl = IntegrationController(runtime, store)
    mgr = RecoveryManager(ctrl, store, synthetic_dataset_path)
    recovered = mgr.recover()
    assert recovered == 1
    got = store.get_run("run_pending")
    assert got.status == RunStatus.SUCCEEDED
    store.close()
