"""Regression tests for the materialized Laptop 1 output boundary."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from integration.laptop1.artifact_manager import ArtifactManager
from integration.laptop1.controller import IntegrationController
from integration.laptop1.models import IntegrationRunRecord, RunStatus
from integration.laptop1.state_store import StateStore


def _phase3_writer(source: str | Path, output_dir: str | Path) -> list[Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    payload = destination / "run__incident__event.json"
    payload.write_text("{}", encoding="utf-8")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    manifest = {"payload_count": 1, "payloads": [
        {"path": payload.name, "sha256": digest}
    ]}
    (destination / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return [payload]


class _Runtime:
    ready = True

    def warm(self) -> None:
        self.ready = True

    def process(self, raw: dict) -> dict:
        return {"incidents": [{
            "event_id": "evt_1", "incident_id": "svc_1",
            "actionability": {"decision": "ACTIONABLE", "reasons": []},
        }]}


def test_complete_boundary_materializes_files_and_final_state(tmp_path: Path) -> None:
    source = tmp_path / "dataset.json"
    source.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metadata": {"git_sha": "abc"}, "incidents": [{}],
    }), encoding="utf-8")
    store = StateStore(tmp_path / "state.db")
    artifacts = ArtifactManager(tmp_path / "artifacts", _phase3_writer)
    record = IntegrationController(_Runtime(), store, artifacts).run_once(
        source, run_id="run_boundary")

    assert record.status == RunStatus.PHASE3_INPUTS_READY
    assert Path(record.phase1_artifact_path).is_file()
    assert Path(record.phase2_output_path).is_file()
    assert Path(record.phase3_manifest_path).is_file()
    assert record.phase3_payload_count == 1
    assert len(store.get_results_for_run(record.run_id)) == 1


def test_result_recovery_is_idempotent_across_threads(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")

    def create(index: int) -> None:
        run_id = f"run_{index}"
        store.create_run(IntegrationRunRecord(
            run_id=run_id, dataset_generated_at="test"))
        store.update_run(run_id, status=RunStatus.RUNNING)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(create, range(50)))
    assert store.count_runs_by_status()[RunStatus.RUNNING.value] == 50


def test_legacy_sqlite_only_success_is_not_misreported(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    store = StateStore(database)
    store.create_run(IntegrationRunRecord(
        run_id="legacy", dataset_generated_at="old", status=RunStatus.SUCCEEDED))
    store.close()
    reopened = StateStore(database)
    assert reopened.get_run("legacy").status == RunStatus.ARTIFACT_BACKFILL_REQUIRED


def test_stop_script_never_assigns_reserved_pid_variable() -> None:
    script = (Path(__file__).resolve().parents[2] / "integration" / "laptop1" /
              "stop-laptop1-integration.ps1").read_text(encoding="utf-8").lower()
    assert "$pid =" not in script
    assert "$processid" in script
