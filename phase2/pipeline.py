"""Phase 2 orchestration: filter, retrieve, enrich, and persist."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from pathlib import Path
from typing import cast, Literal, Any

from .config import PROVISIONAL_MIN_SIMILARITY
from .filtering import ActionabilityPolicy
from .memory import IncidentMemory
from .models import (IndexReport, Phase1Dataset, Phase2BatchOutput,
                     Phase2InputEnvelope, Phase2Result, RetrievalSummary)
from .normalization import incident_fingerprint


class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_record = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if hasattr(record, "incident_id"):
            log_record["incident_id"] = record.incident_id
        if hasattr(record, "status"):
            log_record["status"] = record.status
        return json.dumps(log_record)


logger = logging.getLogger("phase2.pipeline")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)


def _stable_id(*parts: str, prefix: str = "") -> str:
    digest = hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}{digest}"


def build_input_envelopes(dataset: Phase1Dataset) -> list[Phase2InputEnvelope]:
    """Adapt the current Phase 1 batch artifact to the future event boundary."""
    metadata = dataset.metadata or {}
    run_id = _stable_id(
        dataset.generated_at,
        str(metadata.get("git_sha", "unknown")),
        prefix="run_",
    )[:36]
    correlation_id = _stable_id(run_id, "phase1-phase2", prefix="trace_")[:38]
    envelopes: list[Phase2InputEnvelope] = []
    for incident in dataset.incidents:
        event = incident.incident_event
        fingerprint = incident_fingerprint(
            event.target_service, incident.telemetry_evidence.log_cluster_template
        )
        idempotency_key = _stable_id(
            dataset.generated_at, event.incident_id, fingerprint
        )
        envelopes.append(
            Phase2InputEnvelope(
                event_id=_stable_id(idempotency_key, "phase1", prefix="evt_"),
                run_id=run_id,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
                produced_at=dataset.generated_at,
                dataset_generated_at=dataset.generated_at,
                dataset_metadata=metadata,
                incident=incident,
            )
        )
    return envelopes


def _retrieve_history(
    envelope: Phase2InputEnvelope,
    memory: IncidentMemory,
    *,
    top_k: int,
    min_similarity: float,
) -> tuple[list[Any], RetrievalSummary]:
    incident = envelope.incident
    event = incident.incident_event
    template = incident.telemetry_evidence.log_cluster_template

    exact = [
        item
        for item in memory.find_exact(event.target_service, template)
        if item.incident_id != event.incident_id
    ]
    if exact:
        matches = exact[:top_k]
        return matches, RetrievalSummary(
            strategy="EXACT",
            candidates_considered=len(exact),
            matches_returned=len(matches),
            best_similarity=1.0,
            min_similarity=min_similarity,
        )

    same_service = [
        item
        for item in memory.search(
            template,
            target_service=event.target_service,
            query_target_service=event.target_service,
            top_k=top_k,
            min_similarity=min_similarity,
        )
        if item.incident_id != event.incident_id
    ]
    cross_service = [
        item
        for item in memory.search(
            template,
            query_target_service=event.target_service,
            top_k=min(top_k * 2, 50),
            min_similarity=min_similarity,
        )
        if item.incident_id != event.incident_id
        and item.target_service != event.target_service
    ]

    combined = []
    seen: set[tuple[str, str]] = set()
    for item in same_service + cross_service:
        key = (item.incident_id, item.fingerprint)
        if key not in seen:
            combined.append(item)
            seen.add(key)
        if len(combined) == top_k:
            break
    best = max((item.similarity for item in combined), default=None)
    return combined, RetrievalSummary(
        strategy="SEMANTIC" if combined else "NONE",
        candidates_considered=len(same_service) + len(cross_service),
        matches_returned=len(combined),
        best_similarity=best,
        min_similarity=min_similarity,
    )


def evaluate_envelope(
    envelope: Phase2InputEnvelope,
    memory: IncidentMemory,
    policy: ActionabilityPolicy,
    *,
    top_k: int = 5,
    min_similarity: float = PROVISIONAL_MIN_SIMILARITY,
) -> Phase2Result:
    """Evaluate one incident without mutating memory; safe for retries."""
    if not 1 <= top_k <= 50:
        raise ValueError("top_k must be between 1 and 50")
    if not 0.0 <= min_similarity <= 1.0:
        raise ValueError("min_similarity must be between 0 and 1")
    assessment = policy.assess(envelope.incident)
    event = envelope.incident.incident_event
    template = envelope.incident.telemetry_evidence.log_cluster_template
    fingerprint = incident_fingerprint(event.target_service, template)

    if assessment.decision != "ACTIONABLE":
        status = "QUARANTINED" if assessment.decision == "QUARANTINED" else "SKIPPED"
        logger.info(
            "Incident rejected by policy",
            extra={"incident_id": event.incident_id, "status": status},
        )
        return Phase2Result(
            status=cast(Literal['SUCCEEDED', 'SKIPPED', 'QUARANTINED', 'FAILED'], status),
            event_id=_stable_id(envelope.event_id, "phase2-result", prefix="evt_"),
            source_event_id=envelope.event_id,
            run_id=envelope.run_id,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.event_id,
            idempotency_key=_stable_id(envelope.idempotency_key, "phase2-result"),
            incident_id=event.incident_id,
            fingerprint=fingerprint,
            actionability=assessment,
            indexed=False,
            retrieval=RetrievalSummary(
                strategy="NOT_RUN",
                candidates_considered=0,
                matches_returned=0,
                min_similarity=min_similarity,
            ),
            matches=[],
            incident_context=envelope.incident,
        )

    matches, retrieval = _retrieve_history(
        envelope,
        memory,
        top_k=top_k,
        min_similarity=min_similarity,
    )
    logger.info(
        "Incident processing succeeded",
        extra={"incident_id": event.incident_id, "status": "SUCCEEDED"},
    )
    return Phase2Result(
        status="SUCCEEDED",
        event_id=_stable_id(envelope.event_id, "phase2-result", prefix="evt_"),
        source_event_id=envelope.event_id,
        run_id=envelope.run_id,
        correlation_id=envelope.correlation_id,
        causation_id=envelope.event_id,
        idempotency_key=_stable_id(envelope.idempotency_key, "phase2-result"),
        incident_id=event.incident_id,
        fingerprint=fingerprint,
        actionability=assessment,
        indexed=True,
        retrieval=retrieval,
        matches=matches,
        incident_context=envelope.incident,
    )


def process_dataset(
    raw_dataset: dict[str, Any],
    memory: IncidentMemory,
    *,
    top_k: int = 5,
    min_similarity: float = PROVISIONAL_MIN_SIMILARITY,
    policy: ActionabilityPolicy | None = None,
) -> tuple[IndexReport, list[Phase2Result]]:
    dataset = Phase1Dataset.model_validate(raw_dataset)
    active_policy = policy or ActionabilityPolicy.from_environment()
    outputs = [
        evaluate_envelope(
            envelope,
            memory,
            active_policy,
            top_k=top_k,
            min_similarity=min_similarity,
        )
        for envelope in build_input_envelopes(dataset)
    ]

    actionable_ids = {
        result.incident_id
        for result in outputs
        if result.actionability.decision == "ACTIONABLE"
    }
    quarantined_ids = {
        result.incident_id
        for result in outputs
        if result.actionability.decision == "QUARANTINED"
    }
    report = memory.index_dataset(
        raw_dataset,
        eligible_incident_ids=actionable_ids,
        quarantined_incident_ids=quarantined_ids,
    )
    return report, outputs


def process_phase1_dataset(
    raw_dataset: dict[str, Any],
    memory: IncidentMemory,
    *,
    top_k: int = 5,
    min_similarity: float = PROVISIONAL_MIN_SIMILARITY,
    policy: ActionabilityPolicy | None = None,
) -> Phase2BatchOutput:
    report, results = process_dataset(
        raw_dataset, memory, top_k=top_k, min_similarity=min_similarity, policy=policy
    )
    return Phase2BatchOutput(
        source_generated_at=report.dataset_generated_at,
        index_report=report,
        incidents=results,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Filter and enrich a Phase 1 dataset")
    parser.add_argument(
        "--dataset", default="frontend_data/unified_master_dataset.json"
    )
    parser.add_argument("--output", default="frontend_data/phase2_enriched.json")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--min-similarity", type=float, default=PROVISIONAL_MIN_SIMILARITY
    )
    args = parser.parse_args()

    raw_dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    payload = process_phase1_dataset(
        raw_dataset,
        IncidentMemory(),
        top_k=args.top_k,
        min_similarity=args.min_similarity,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(output_path)
    print(payload.index_report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
