"""Strict Phase 3 input boundary models.

These models define the validated contract that Phase 2 emits for every
actionable incident. They are intentionally strict (``extra="forbid"``) so that
any schema drift or accidental field leakage is rejected at validation time.

Phase 2 behavior, retrieval logic, similarity calculations, ChromaDB indexing,
lifecycle operations, MCP worker logic, and CI configuration are NOT modified by
this module. It only adds the Phase 3 output boundary.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from phase2.models import (
    ActionabilityAssessment,
    IncidentEvent,
    Phase2Match,
    RetrievalSummary,
    TelemetryEvidence,
)


class Phase3Source(BaseModel):
    """Provenance identifiers linking a Phase 3 input back to its Phase 2 result."""

    model_config = ConfigDict(extra="forbid")

    phase: Literal["phase2"] = "phase2"
    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    causation_id: str = Field(min_length=1)


class Phase3Context(BaseModel):
    """Phase 2 evidence bundle carried into Phase 3."""

    model_config = ConfigDict(extra="forbid")

    actionability: ActionabilityAssessment
    retrieval: RetrievalSummary
    historical_memory_evidence: list[Phase2Match] = Field(default_factory=list)


class Phase3Input(BaseModel):
    """Strictly validated Phase 3 input document for a single actionable incident."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    source: Phase3Source
    system_context: dict[str, Any] = Field(default_factory=dict)
    incident_event: IncidentEvent
    infrastructure_topology: dict[str, Any] = Field(default_factory=dict)
    service_health_status: dict[str, Any] = Field(default_factory=dict)
    telemetry_evidence: TelemetryEvidence
    injected_chaos_context: dict[str, Any] = Field(default_factory=dict)
    phase2_context: Phase3Context
    agent_instruction: str = Field(min_length=1)
