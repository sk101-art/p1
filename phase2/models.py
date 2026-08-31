"""Versioned boundary models for Phase 1 input and Phase 2 output."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IncidentEvent(BaseModel):
    """The Phase 1 incident identity and prioritization result."""

    model_config = ConfigDict(extra="allow")
    incident_id: str = Field(min_length=1)
    target_service: str
    priority_score: float = Field(default=0.0, ge=0.0)
    severity: str = "LOW"
    occurrence_count: int = Field(default=1, ge=0)

    @field_validator("severity")
    @classmethod
    def normalize_severity(cls, value: str) -> str:
        return value.strip().upper() or "LOW"


class LogSample(BaseModel):
    model_config = ConfigDict(extra="allow")
    timestamp: str
    level: str
    content: str
    trace_id: str | None = None
    span_id: str | None = None


class TelemetryEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    log_cluster_template: str
    log_samples: list[LogSample] = Field(default_factory=list, max_length=5)
    metrics_snapshot: list[dict[str, Any]] = Field(default_factory=list, max_length=3)


class Phase1Incident(BaseModel):
    """Compatible reader for the additive Phase 1 incident contract."""

    model_config = ConfigDict(extra="allow")
    incident_event: IncidentEvent
    telemetry_evidence: TelemetryEvidence
    system_context: dict[str, Any] = Field(default_factory=dict)
    infrastructure_topology: dict[str, Any] = Field(default_factory=dict)
    service_health_status: dict[str, Any] = Field(default_factory=dict)
    injected_chaos_context: dict[str, Any] = Field(default_factory=dict)


class Phase1Dataset(BaseModel):
    """Batch compatibility adapter for the existing Phase 1 artifact."""

    model_config = ConfigDict(extra="allow")
    generated_at: str
    metadata: dict[str, Any] | None = None
    system_context: dict[str, Any] = Field(default_factory=dict)
    incidents: list[Phase1Incident]


class Phase2InputEnvelope(BaseModel):
    """Canonical per-incident boundary used by files, HTTP, or an event bus."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    event_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    run_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    causation_id: str | None = None
    idempotency_key: str = Field(min_length=1)
    source: Literal["phase1"] = "phase1"
    produced_at: str
    dataset_generated_at: str
    dataset_metadata: dict[str, Any] = Field(default_factory=dict)
    incident: Phase1Incident


class ActionabilityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["ACTIONABLE", "NON_ACTIONABLE", "QUARANTINED"]
    score: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(min_length=1)
    signals: dict[str, float] = Field(default_factory=dict)
    policy_version: str = "1.0"


class Phase2Match(BaseModel):
    model_config = ConfigDict(extra="forbid")
    incident_id: str
    fingerprint: str
    match_type: Literal["exact", "semantic"]
    retrieval_scope: Literal["same_service", "cross_service"] = "same_service"
    target_service: str
    severity: str
    log_cluster_template: str
    distance: float = Field(ge=0.0)
    similarity: float = Field(ge=0.0, le=1.0)
    trace_ids: list[str] = Field(default_factory=list)


Phase2RetrievedIncident = Phase2Match


class RetrievalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: Literal["EXACT", "SEMANTIC", "NONE", "NOT_RUN"]
    candidates_considered: int = Field(ge=0)
    matches_returned: int = Field(ge=0)
    best_similarity: float | None = Field(default=None, ge=0.0, le=1.0)
    min_similarity: float = Field(ge=0.0, le=1.0)


class Phase2Result(BaseModel):
    """Complete evidence package consumed by Phase 3 and the RL matcher."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    phase: Literal["phase2"] = "phase2"
    event_type: Literal["phase2.incident.enriched"] = "phase2.incident.enriched"
    status: Literal["SUCCEEDED", "SKIPPED", "QUARANTINED", "FAILED"]
    event_id: str
    source_event_id: str
    run_id: str
    correlation_id: str
    causation_id: str
    idempotency_key: str
    incident_id: str
    fingerprint: str
    actionability: ActionabilityAssessment
    indexed: bool
    retrieval: RetrievalSummary
    matches: list[Phase2Match] = Field(default_factory=list)
    incident_context: Phase1Incident
    produced_at: str = Field(default_factory=utc_now)


class IndexReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    received: int = Field(ge=0)
    indexed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    quarantined: int = Field(ge=0)
    dataset_generated_at: str


class Phase2BatchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    phase: Literal["phase2"] = "phase2"
    event_type: Literal["phase2.batch.completed"] = "phase2.batch.completed"
    source_generated_at: str
    index_report: IndexReport
    incidents: list[Phase2Result]
    produced_at: str = Field(default_factory=utc_now)
