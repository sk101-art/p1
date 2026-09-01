from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RunStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PHASE3_INPUTS_READY = "PHASE3_INPUTS_READY"
    ARTIFACT_BACKFILL_REQUIRED = "ARTIFACT_BACKFILL_REQUIRED"
    FAILED = "FAILED"
    RECOVERED = "RECOVERED"


class Phase2Outcome(str, enum.Enum):
    ACTIONABLE = "ACTIONABLE"
    NON_ACTIONABLE = "NON_ACTIONABLE"
    QUARANTINED = "QUARANTINED"
    ERROR = "ERROR"


class IntegrationRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    dataset_generated_at: str
    dataset_git_sha: Optional[str] = None
    dataset_incident_count: int = Field(default=0, ge=0)
    status: RunStatus = RunStatus.PENDING
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    phase2_outcome_counts: Dict[str, int] = Field(default_factory=dict)
    phase1_artifact_path: Optional[str] = None
    phase1_sha256: Optional[str] = None
    phase2_output_path: Optional[str] = None
    phase2_sha256: Optional[str] = None
    phase3_manifest_path: Optional[str] = None
    phase3_payload_count: int = Field(default=0, ge=0)
    error: Optional[str] = None
    recovery_attempts: int = Field(default=0, ge=0)
    created_at: str = Field(default_factory=_utcnow_iso)


class NotificationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event: Literal["phase1_dataset_packaged"] = "phase1_dataset_packaged"
    dataset_path: str
    generated_at: str
    git_sha: Optional[str] = None
    incident_count: int = Field(default=0, ge=0)
    notifier_version: str = "1.0"
    emitted_at: str = Field(default_factory=_utcnow_iso)

    @field_validator("dataset_path")
    @classmethod
    def _non_empty_path(cls, value: str) -> str:
        if not value:
            raise ValueError("dataset_path must be non-empty")
        return value


class PipelineStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: str = "laptop1-phase1-phase2-integration"
    healthy: bool = True
    last_run_id: Optional[str] = None
    last_run_status: Optional[RunStatus] = None
    last_run_finished_at: Optional[str] = None
    phase2_ready: bool = False
    pending_runs: int = Field(default=0, ge=0)
    uptime_seconds: float = Field(default=0.0, ge=0)
    checked_at: str = Field(default_factory=_utcnow_iso)


class Phase2ResultSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str
    run_id: str
    correlation_id: Optional[str] = None
    incident_id: Optional[str] = None
    outcome: Phase2Outcome
    decision: Optional[str] = None
    target_service: Optional[str] = None
    severity: Optional[str] = None
    matched_incident_id: Optional[str] = None
    similarity: Optional[float] = None
    reasons: List[str] = Field(default_factory=list)
    agent_instruction: Optional[str] = None


class TriggerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_path: Optional[str] = None
    force: bool = False


class TriggerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accepted: bool
    run_id: Optional[str] = None
    detail: str = ""


__all__ = [
    "RunStatus", "Phase2Outcome", "IntegrationRunRecord", "NotificationPayload",
    "PipelineStatus", "Phase2ResultSummary", "TriggerRequest", "TriggerResponse",
]
