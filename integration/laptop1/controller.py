"""Integration controller: orchestrates a single Phase1->Phase2 run.

The controller is the durable brain of the bridge. For each run it:
1. Loads the canonical Phase 1 dataset (with a freshness guard).
2. Creates a durable run record (PENDING -> RUNNING).
3. Invokes the frozen Phase 2 runtime.
4. Persists per-incident Phase 2 result summaries.
5. Marks the run SUCCEEDED / FAILED and records outcome counts.

All state transitions are written to the StateStore so recovery can resume.
"""

from __future__ import annotations

import json
import os
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .models import (
    IntegrationRunRecord,
    Phase2Outcome,
    Phase2ResultSummary,
    RunStatus,
)
from .phase2_runtime import Phase2Runtime
from .state_store import StateStore

# A dataset older than this (seconds) is rejected as stale.
DATASET_MAX_AGE_SECONDS = int(os.getenv("LAPTOP1_DATASET_MAX_AGE_SECONDS", "86400"))


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> Optional[float]:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return None


class IntegrationController:
    def __init__(self, runtime: Phase2Runtime, store: StateStore) -> None:
        self.runtime = runtime
        self.store = store

    def _load_dataset(self, dataset_path: str | Path) -> tuple[dict[str, Any], str, Optional[str], int]:
        path = Path(dataset_path)
        if not path.exists():
            raise FileNotFoundError(f"Phase 1 dataset not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        generated_at = raw.get("generated_at")
        if not generated_at:
            raise ValueError("Dataset missing 'generated_at'")
        ts = _parse_iso(generated_at)
        if ts is not None:
            age = time.time() - ts
            if age > DATASET_MAX_AGE_SECONDS:
                raise ValueError(
                    f"Dataset stale: age {age:.0f}s exceeds max {DATASET_MAX_AGE_SECONDS}s"
                )

        git_sha = None
        incident_count = 0
        meta = raw.get("metadata")
        if isinstance(meta, dict):
            git_sha = meta.get("git_sha")
        incidents = raw.get("incidents") or []
        incident_count = len(incidents)
        return raw, generated_at, git_sha, incident_count

    def run_once(
        self,
        dataset_path: str | Path,
        *,
        run_id: Optional[str] = None,
    ) -> IntegrationRunRecord:
        run_id = run_id or f"run_{uuid.uuid4().hex}"

        # Create a PENDING record first so failures are durably recorded.
        record = IntegrationRunRecord(
            run_id=run_id,
            dataset_generated_at="",
            dataset_incident_count=0,
            status=RunStatus.PENDING,
        )
        self.store.create_run(record)

        try:
            raw, generated_at, git_sha, incident_count = self._load_dataset(dataset_path)

            # Update record with actual dataset metadata.
            self.store.update_run(
                run_id,
                dataset_generated_at=generated_at,
                dataset_git_sha=git_sha,
                dataset_incident_count=incident_count,
                status=RunStatus.RUNNING,
                started_at=_utcnow_iso(),
            )

            if not self.runtime.ready:
                self.runtime.warm()
            batch: Any = self.runtime.process(raw)

            outcome_counts: dict[str, int] = {}
            results = getattr(batch, "incidents", None) or []
            for res in results:
                outcome = self._classify(res)
                outcome_counts[outcome.value] = outcome_counts.get(outcome.value, 0) + 1
                self.store.insert_result(
                    self._summarize(run_id, res, outcome)
                )

            self.store.update_run(
                run_id,
                status=RunStatus.SUCCEEDED,
                finished_at=_utcnow_iso(),
                phase2_outcome_counts=outcome_counts,
            )
            record.status = RunStatus.SUCCEEDED
            record.phase2_outcome_counts = outcome_counts
            return record

        except Exception as exc:  # noqa: BLE001 - durable capture
            self.store.update_run(
                run_id,
                status=RunStatus.FAILED,
                finished_at=_utcnow_iso(),
                error=f"{type(exc).__name__}: {exc}",
            )
            record.status = RunStatus.FAILED
            record.error = f"{type(exc).__name__}: {exc}"
            # Surface for logs but do not crash the service loop.
            traceback.print_exc()
            return record

    @staticmethod
    def _classify(res: Any) -> Phase2Outcome:
        # Phase2Result carries the decision inside .actionability.decision.
        decision = None
        actionability = getattr(res, "actionability", None)
        if actionability is not None:
            decision = getattr(actionability, "decision", None)
        if decision is None and isinstance(res, dict):
            actionability = res.get("actionability")
            if isinstance(actionability, dict):
                decision = actionability.get("decision")
            else:
                decision = res.get("decision")
        if decision in ("ACTIONABLE", "PROVISIONAL"):
            return Phase2Outcome.ACTIONABLE
        if decision in ("QUARANTINED",):
            return Phase2Outcome.QUARANTINED
        if decision in ("NON_ACTIONABLE", "BENIGN"):
            return Phase2Outcome.NON_ACTIONABLE
        return Phase2Outcome.ERROR

    @staticmethod
    def _summarize(run_id: str, res: Any, outcome: Phase2Outcome) -> Phase2ResultSummary:
        def _get(attr: str, default=None):
            if hasattr(res, attr):
                return getattr(res, attr)
            if isinstance(res, dict):
                return res.get(attr, default)
            return default

        # Phase2Result stores matches in a list; take the best (highest similarity).
        matches = _get("matches") or []
        match = matches[0] if matches else None
        matched_id = None
        similarity = None
        if match is not None:
            matched_id = getattr(match, "incident_id", None) or (
                match.get("incident_id") if isinstance(match, dict) else None
            )
            similarity = getattr(match, "similarity", None) or (
                match.get("similarity") if isinstance(match, dict) else None
            )

        # Decision lives under actionability.
        actionability = _get("actionability")
        decision = None
        if actionability is not None:
            decision = getattr(actionability, "decision", None) or (
                actionability.get("decision") if isinstance(actionability, dict) else None
            )

        event_id = _get("event_id") or f"evt_{run_id}"
        incident_id = _get("incident_id")
        target_service = None
        severity = None
        reasons = []
        agent_instruction = None
        ctx = _get("incident_context")
        if ctx is not None:
            ev = getattr(ctx, "incident_event", None) or (
                ctx.get("incident_event") if isinstance(ctx, dict) else None
            )
            if ev is not None:
                target_service = getattr(ev, "target_service", None) or (
                    ev.get("target_service") if isinstance(ev, dict) else None
                )
                severity = getattr(ev, "severity", None) or (
                    ev.get("severity") if isinstance(ev, dict) else None
                )
        if actionability is not None:
            reasons = list(
                getattr(actionability, "reasons", []) or (
                    actionability.get("reasons", []) if isinstance(actionability, dict) else []
                )
            )

        return Phase2ResultSummary(
            event_id=str(event_id),
            run_id=run_id,
            outcome=outcome,
            decision=decision,
            target_service=target_service,
            severity=severity,
            matched_incident_id=matched_id,
            similarity=similarity,
            reasons=reasons,
            agent_instruction=agent_instruction,
            incident_id=incident_id,
        )


__all__ = ["IntegrationController"]
