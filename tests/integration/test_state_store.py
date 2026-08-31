"""Tests for the durable StateStore."""
from __future__ import annotations

from pathlib import Path

from integration.laptop1.models import (
    IntegrationRunRecord,
    Phase2Outcome,
    Phase2ResultSummary,
    RunStatus,
)
from integration.laptop1.state_store import StateStore


def _make_run(run_id: str, status: RunStatus = RunStatus.PENDING) -> IntegrationRunRecord:
    return IntegrationRunRecord(
        run_id=run_id,
        dataset_generated_at="2026-08-18T02:53:37Z",
        dataset_git_sha="abc123",
        dataset_incident_count=3,
        status=status,
    )


def test_create_and_get_run(state_db_path: Path):
    store = StateStore(state_db_path)
    rec = _make_run("run_1")
    store.create_run(rec)
    got = store.get_run("run_1")
    assert got is not None
    assert got.run_id == "run_1"
    assert got.status == RunStatus.PENDING
    assert got.dataset_incident_count == 3
    store.close()


def test_update_run_transitions(state_db_path: Path):
    store = StateStore(state_db_path)
    store.create_run(_make_run("run_2"))
    store.update_run("run_2", status=RunStatus.RUNNING, started_at="2026-08-18T03:00:00Z")
    store.update_run("run_2", status=RunStatus.SUCCEEDED, finished_at="2026-08-18T03:05:00Z",
                     phase2_outcome_counts={"ACTIONABLE": 2, "NON_ACTIONABLE": 1})
    got = store.get_run("run_2")
    assert got.status == RunStatus.SUCCEEDED
    assert got.phase2_outcome_counts == {"ACTIONABLE": 2, "NON_ACTIONABLE": 1}
    store.close()


def test_get_last_and_pending(state_db_path: Path):
    store = StateStore(state_db_path)
    store.create_run(_make_run("run_a", RunStatus.SUCCEEDED))
    store.create_run(_make_run("run_b", RunStatus.PENDING))
    store.create_run(_make_run("run_c", RunStatus.RUNNING))
    last = store.get_last_run()
    assert last.run_id == "run_c"
    pending = store.get_pending_runs()
    ids = {r.run_id for r in pending}
    assert ids == {"run_b", "run_c"}
    store.close()


def test_insert_and_query_results(state_db_path: Path):
    store = StateStore(state_db_path)
    store.create_run(_make_run("run_r"))
    summary = Phase2ResultSummary(
        event_id="evt_1",
        run_id="run_r",
        incident_id="auth-service_1",
        outcome=Phase2Outcome.ACTIONABLE,
        decision="ACTIONABLE",
        target_service="auth-service",
        severity="HIGH",
        matched_incident_id="auth-service_9",
        similarity=0.91,
        reasons=["high_priority_score"],
    )
    store.insert_result(summary)
    results = store.get_results_for_run("run_r")
    assert len(results) == 1
    assert results[0].incident_id == "auth-service_1"
    assert results[0].outcome == Phase2Outcome.ACTIONABLE
    assert results[0].similarity == 0.91
    store.close()


def test_count_runs_by_status(state_db_path: Path):
    store = StateStore(state_db_path)
    store.create_run(_make_run("x1", RunStatus.SUCCEEDED))
    store.create_run(_make_run("x2", RunStatus.SUCCEEDED))
    store.create_run(_make_run("x3", RunStatus.FAILED))
    counts = store.count_runs_by_status()
    assert counts.get("SUCCEEDED") == 2
    assert counts.get("FAILED") == 1
    store.close()


def test_schema_migration_adds_incident_id(state_db_path: Path):
    """A pre-existing phase2_results table without incident_id must self-heal."""
    import sqlite3

    # Create an old-style table without incident_id.
    state_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(state_db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, "
        "dataset_generated_at TEXT, dataset_git_sha TEXT, dataset_incident_count INTEGER, "
        "status TEXT, started_at TEXT, finished_at TEXT, phase2_outcome_counts TEXT, "
        "error TEXT, recovery_attempts INTEGER, created_at TEXT);"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS phase2_results (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "run_id TEXT, event_id TEXT, outcome TEXT, decision TEXT, target_service TEXT, "
        "severity TEXT, matched_incident_id TEXT, similarity REAL, reasons TEXT, "
        "agent_instruction TEXT);"
    )
    conn.commit()
    conn.close()

    # Opening the store should migrate the schema without error.
    store = StateStore(state_db_path)
    store.create_run(_make_run("mig_1"))
    store.insert_result(
        Phase2ResultSummary(
            event_id="evt_m", run_id="mig_1", incident_id="svc_1",
            outcome=Phase2Outcome.ACTIONABLE,
        )
    )
    res = store.get_results_for_run("mig_1")
    assert res[0].incident_id == "svc_1"
    store.close()
