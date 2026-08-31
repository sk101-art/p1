"""Explainable defense-in-depth filtering for actionable incidents."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass

from .models import ActionabilityAssessment, Phase1Incident
from typing import cast, Literal

_ERROR_TERMS = re.compile(
    r"\b(error|exception|failed|failure|timeout|refused|unavailable|"
    r"exhausted|deadlock|oom|out of memory|panic|fatal|denied)\b",
    re.IGNORECASE,
)
_BENIGN_TERMS = re.compile(
    r"\b(health check passed|heartbeat received|metrics scrape (?:ok|succeeded)|"
    r"connection established|startup complete|request completed successfully)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ActionabilityPolicy:
    threshold: float = 0.35
    version: str = "1.0"

    @classmethod
    def from_environment(cls) -> "ActionabilityPolicy":
        value = float(os.getenv("PHASE2_ACTIONABILITY_THRESHOLD", "0.35"))
        if not 0.0 <= value <= 1.0:
            raise ValueError("PHASE2_ACTIONABILITY_THRESHOLD must be between 0 and 1")
        return cls(threshold=value)

    def assess(self, incident: Phase1Incident) -> ActionabilityAssessment:
        event = incident.incident_event
        evidence = incident.telemetry_evidence
        template = evidence.log_cluster_template.strip()
        service = event.target_service.strip()
        if not service or not template:
            missing = []
            if not service:
                missing.append("target_service")
            if not template:
                missing.append("log_cluster_template")
            return ActionabilityAssessment(
                decision="QUARANTINED",
                score=0.0,
                threshold=self.threshold,
                reasons=["missing_required_signal:" + ",".join(missing)],
                signals={},
                policy_version=self.version,
            )

        signals: dict[str, float] = {}
        reasons: list[str] = []
        severity_weight = {
            "CRITICAL": 0.45,
            "HIGH": 0.35,
            "MEDIUM": 0.22,
            "LOW": 0.08,
        }.get(event.severity.upper(), 0.05)
        signals["severity"] = severity_weight
        reasons.append(f"severity:{event.severity.upper()}")

        priority_weight = min(float(event.priority_score), 100.0) / 100.0 * 0.25
        signals["priority"] = priority_weight
        if priority_weight >= 0.15:
            reasons.append("elevated_priority")

        occurrence_weight = (
            min(math.log1p(max(event.occurrence_count, 0)) / math.log1p(20), 1.0) * 0.12
        )
        signals["recurrence"] = occurrence_weight
        if event.occurrence_count >= 3:
            reasons.append("repeated_occurrence")

        health = incident.service_health_status
        docker_status = str(health.get("docker_status", "unknown")).casefold()
        health_check = str(health.get("health_check", "unknown")).casefold()
        unhealthy_dependencies = sum(
            1
            for value in (health.get("dependency_states") or {}).values()
            if str(value).casefold() not in {"healthy", "running", "up", "ok"}
        )
        health_weight = 0.0
        if docker_status not in {"running", "up", "healthy", "unknown"}:
            health_weight += 0.25
            reasons.append("service_not_running")
        if health_check not in {"healthy", "ok", "up", "unknown"}:
            health_weight += 0.20
            reasons.append("health_check_failed")
        if unhealthy_dependencies:
            health_weight += min(unhealthy_dependencies * 0.05, 0.15)
            reasons.append("dependency_degraded")
        signals["health"] = health_weight

        levels = {sample.level.upper() for sample in evidence.log_samples}
        level_weight = (
            0.15 if levels & {"ERROR", "FATAL"} else 0.08 if "WARN" in levels else 0.0
        )
        signals["log_level"] = level_weight
        if level_weight:
            reasons.append("error_level_telemetry")

        semantic_weight = 0.12 if _ERROR_TERMS.search(template) else 0.0
        signals["error_semantics"] = semantic_weight
        if semantic_weight:
            reasons.append("failure_semantics")

        chaos = str(
            incident.injected_chaos_context.get("active_infrastructure_mutations", "")
        ).strip()
        chaos_weight = 0.08 if chaos else 0.0
        signals["correlated_chaos"] = chaos_weight
        if chaos_weight:
            reasons.append("correlated_infrastructure_mutation")

        score = sum(signals.values())
        safely_benign = (
            bool(_BENIGN_TERMS.search(template))
            and event.severity.upper() == "LOW"
            and not levels.intersection({"ERROR", "FATAL"})
            and health_weight == 0.0
        )
        if safely_benign:
            signals["benign_noise_penalty"] = -0.55
            score -= 0.55
            reasons.append("known_benign_operational_noise")

        score = round(max(0.0, min(1.0, score)), 6)
        decision = "ACTIONABLE" if score >= self.threshold else "NON_ACTIONABLE"
        reasons.append(
            "score_meets_threshold"
            if decision == "ACTIONABLE"
            else "score_below_threshold"
        )
        return ActionabilityAssessment(
            decision=cast(Literal['ACTIONABLE', 'NON_ACTIONABLE', 'QUARANTINED'], decision),
            score=score,
            threshold=self.threshold,
            reasons=reasons,
            signals={key: round(value, 6) for key, value in signals.items()},
            policy_version=self.version,
        )
