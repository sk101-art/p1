"""Durable Phase 1 -> frozen Phase 2 -> Phase 3 boundary orchestration."""
from __future__ import annotations

import json
import os
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .artifact_manager import ArtifactManager
from .models import IntegrationRunRecord, Phase2Outcome, Phase2ResultSummary, RunStatus
from .state_store import StateStore

DATASET_MAX_AGE_SECONDS = int(os.getenv("LAPTOP1_DATASET_MAX_AGE_SECONDS", "86400"))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> Optional[float]:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


class IntegrationController:
    def __init__(self, runtime: Any, store: StateStore,
                 artifacts: Optional[ArtifactManager] = None) -> None:
        self.runtime = runtime
        self.store = store
        self.artifacts = artifacts

    def _load_dataset(self, dataset_path: str | Path) -> tuple[dict[str, Any], str, Optional[str], int]:
        path = Path(dataset_path)
        if not path.exists():
            raise FileNotFoundError(f"Phase 1 dataset not found: {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        generated_at = raw.get("generated_at")
        if not generated_at:
            raise ValueError("Dataset missing 'generated_at'")
        timestamp = _parse_iso(generated_at)
        if timestamp is not None and time.time() - timestamp > DATASET_MAX_AGE_SECONDS:
            raise ValueError("Dataset stale: generated_at exceeds configured maximum age")
        metadata = raw.get("metadata")
        git_sha = metadata.get("git_sha") if isinstance(metadata, dict) else None
        incidents = raw.get("incidents") or []
        return raw, generated_at, git_sha, len(incidents)

    def run_once(self, dataset_path: str | Path, *,
                 run_id: Optional[str] = None) -> IntegrationRunRecord:
        run_id = run_id or f"run_{uuid.uuid4().hex}"
        existing = self.store.get_run(run_id)
        if existing and existing.status == RunStatus.PHASE3_INPUTS_READY:
            return existing
        self.store.create_run(IntegrationRunRecord(
            run_id=run_id, dataset_generated_at="", status=RunStatus.PENDING))

        try:
            # A crash after the durable Phase 2 file was recorded resumes only
            # the cheap Phase 3 materialization step; Phase 2 is not repeated.
            existing = self.store.get_run(run_id)
            if (self.artifacts and existing and existing.phase2_output_path
                    and Path(existing.phase2_output_path).is_file()):
                phase3 = self.artifacts.write_phase3_payloads(
                    run_id, existing.phase2_output_path)
                self.store.update_run(
                    run_id, status=RunStatus.PHASE3_INPUTS_READY,
                    finished_at=_utcnow_iso(),
                    phase3_manifest_path=phase3["manifest_path"],
                    phase3_payload_count=phase3["payload_count"], error=None)
                return self.store.get_run(run_id)  # type: ignore[return-value]

            raw, generated_at, git_sha, incident_count = self._load_dataset(dataset_path)
            update: dict[str, Any] = {
                "dataset_generated_at": generated_at,
                "dataset_git_sha": git_sha,
                "dataset_incident_count": incident_count,
                "status": RunStatus.RUNNING,
                "started_at": existing.started_at if existing and existing.started_at else _utcnow_iso(),
                "finished_at": None,
                "error": None,
            }
            if self.artifacts:
                phase1 = self.artifacts.snapshot_dataset(run_id, dataset_path)
                update.update(phase1_artifact_path=phase1["path"],
                              phase1_sha256=phase1["sha256"])
            self.store.update_run(run_id, **update)

            if not self.runtime.ready:
                self.runtime.warm()
            batch = self.runtime.process(raw)
            results = getattr(batch, "incidents", None)
            if results is None and isinstance(batch, dict):
                results = batch.get("incidents")
            results = results or []
            outcome_counts: dict[str, int] = {}
            for result in results:
                outcome = self._classify(result)
                outcome_counts[outcome.value] = outcome_counts.get(outcome.value, 0) + 1
                self.store.insert_result(self._summarize(run_id, result, outcome))

            if not self.artifacts:
                self.store.update_run(
                    run_id, status=RunStatus.SUCCEEDED, finished_at=_utcnow_iso(),
                    phase2_outcome_counts=outcome_counts)
                return self.store.get_run(run_id)  # type: ignore[return-value]

            phase2 = self.artifacts.write_phase2_batch(run_id, batch)
            self.store.update_run(
                run_id, phase2_outcome_counts=outcome_counts,
                phase2_output_path=phase2["path"], phase2_sha256=phase2["sha256"])
            phase3 = self.artifacts.write_phase3_payloads(run_id, phase2["path"])
            self.store.update_run(
                run_id, status=RunStatus.PHASE3_INPUTS_READY,
                finished_at=_utcnow_iso(),
                phase3_manifest_path=phase3["manifest_path"],
                phase3_payload_count=phase3["payload_count"])
            return self.store.get_run(run_id)  # type: ignore[return-value]
        except Exception as exc:  # noqa: BLE001
            self.store.update_run(
                run_id, status=RunStatus.FAILED, finished_at=_utcnow_iso(),
                error=f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
            return self.store.get_run(run_id)  # type: ignore[return-value]

    @staticmethod
    def _classify(result: Any) -> Phase2Outcome:
        actionability = getattr(result, "actionability", None)
        if actionability is None and isinstance(result, dict):
            actionability = result.get("actionability")
        decision = getattr(actionability, "decision", None)
        if decision is None and isinstance(actionability, dict):
            decision = actionability.get("decision")
        if decision in ("ACTIONABLE", "PROVISIONAL"):
            return Phase2Outcome.ACTIONABLE
        if decision == "QUARANTINED":
            return Phase2Outcome.QUARANTINED
        if decision in ("NON_ACTIONABLE", "BENIGN"):
            return Phase2Outcome.NON_ACTIONABLE
        return Phase2Outcome.ERROR

    @staticmethod
    def _summarize(run_id: str, result: Any,
                   outcome: Phase2Outcome) -> Phase2ResultSummary:
        def get(value: Any, name: str, default: Any = None) -> Any:
            return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)

        matches = get(result, "matches", []) or []
        match = matches[0] if matches else None
        actionability = get(result, "actionability")
        context = get(result, "incident_context")
        event = get(context, "incident_event") if context is not None else None
        return Phase2ResultSummary(
            event_id=str(get(result, "event_id") or f"evt_{run_id}"),
            run_id=run_id, incident_id=get(result, "incident_id"), outcome=outcome,
            decision=get(actionability, "decision") if actionability is not None else None,
            target_service=get(event, "target_service") if event is not None else None,
            severity=get(event, "severity") if event is not None else None,
            matched_incident_id=get(match, "incident_id") if match is not None else None,
            similarity=get(match, "similarity") if match is not None else None,
            reasons=list(get(actionability, "reasons", []) or []) if actionability is not None else [],
            agent_instruction=None)


__all__ = ["IntegrationController"]
