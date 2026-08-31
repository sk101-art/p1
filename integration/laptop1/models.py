"""Integration boundary event contracts for the laptop1 Phase1<->Phase2 bridge.

These models define the durable, validated contract between the Phase 1
packaging step, the local orchestration service, and the frozen Phase 2
runtime. Every model uses ``extra="forbid"`` so that unexpected fields are
rejected at the boundary instead of silently passing through.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RunStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RECOVERED = "RECOVERED"


class Phase2Outcome(str, enum.Enum):
    ACTIONABLE = "ACTIONABLE"
    NON_ACTIONABLE = "NON_ACTIONABLE"
    QUARANTINED = "QUARANTINED"
    ERROR = "ERROR"


class IntegrationRunRecord(BaseModel):
    """Durable record of a single Phase1->Phase2 processing run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., description="Deterministic UUID for this run")
    dataset_generated_at: str = Field(..., description="Phase1 dataset generated_at")
    dataset_git_sha: Optional[str] = Field(default=None)
    dataset_incident_count: int = Field(default=0, ge=0)
    status: RunStatus = Field(default=RunStatus.PENDING)
    started_at: Optional[str] = Field(default=None)
    finished_at: Optional[str] = Field(default=None)
    phase2_outcome_counts: Dict[str, int] = Field(default_factory=dict)
    error: Optional[str] = Field(default=None)
    recovery_attempts: int = Field(default=0, ge=0)
    created_at: str = Field(default_factory=_utcnow_iso)


class NotificationPayload(BaseModel):
    """Single post-commit notification emitted by the Phase 1 notifier."""

    model_config = ConfigDict(extra="forbid")

    event: Literal["phase1_dataset_packaged"] = "phase1_dataset_packaged"
    dataset_path: str = Field(..., description="Absolute path to unified_master_dataset.json")
    generated_at: str = Field(..., description="Phase1 dataset generated_at timestamp")
    git_sha: Optional[str] = Field(default=None)
    incident_count: int = Field(default=0, ge=0)
    notifier_version: str = Field(default="1.0")
    emitted_at: str = Field(default_factory=_utcnow_iso)

    @field_validator("dataset_path")
    @classmethod
    def _abs_path(cls, v: str) -> str:
        if not v:
            raise ValueError("dataset_path must be non-empty")
        return v


class PipelineStatus(BaseModel):
    """Health/status snapshot returned by the integration service."""

    model_config = ConfigDict(extra="forbid")

    service: str = "laptop1-phase1-phase2-integration"
    healthy: bool = True
    last_run_id: Optional[str] = Field(default=None)
    last_run_status: Optional[RunStatus] = Field(default=None)
    last_run_finished_at: Optional[str] = Field(default=None)
    phase2_ready: bool = False
    pending_runs: int = Field(default=0, ge=0)
    uptime_seconds: float = Field(default=0.0, ge=0)
    checked_at: str = Field(default_factory=_utcnow_iso)


class Phase2ResultSummary(BaseModel):
    """Compact, validated summary of one Phase 2 result for storage."""

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
    """Manual trigger request accepted by the API."""

    model_config = ConfigDict(extra="forbid")

    dataset_path: Optional[str] = Field(default=None)
    force: bool = Field(default=False)


class TriggerResponse(BaseModel):
    """Response to a manual trigger request."""

    model_config = ConfigDict(extra="forbid")

    accepted: bool
    run_id: Optional[str] = None
    detail: str = ""


__all__ = [
    "RunStatus",
    "Phase2Outcome",
    "IntegrationRunRecord",
    "NotificationPayload",
    "PipelineStatus",
    "Phase2ResultSummary",
    "TriggerRequest",
    "TriggerResponse",
]
