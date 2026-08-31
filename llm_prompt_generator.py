"""Build strictly validated Phase 3 input documents from the canonical Phase 2 evidence contract.

This module implements the Phase 2 -> Phase 3 output boundary. It does NOT modify
Phase 1 behavior, retrieval logic, similarity calculations, ChromaDB indexing,
lifecycle operations, MCP worker logic, or CI configuration.

Phase 2 continues to write the canonical ``Phase2BatchOutput`` to
``frontend_data/phase2_enriched.json`` unchanged. This module additionally emits
one strictly validated ``Phase3Input`` JSON document per actionable Phase 2
result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from phase2.models import Phase2BatchOutput, Phase2Result
from phase2.normalization import redact_nested
from phase2.phase3_contract import Phase3Input

_AGENT_INSTRUCTION = (
    "Analyze only the supplied telemetry and historical evidence. "
    "Determine the root cause for {target_service}, cite concrete evidence, "
    "distinguish same-service from cross-service history, state uncertainty, "
    "and return a typed remediation proposal for shadow validation. "
    "Historical similarity is evidence, not execution approval."
)

_UNSAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_filename_component(value: str) -> str:
    """Sanitize a filename component to prevent path traversal.

    Only alphanumeric characters, dots, dashes and underscores are kept. Any
    sequence of unsafe characters is collapsed to a single underscore, and the
    result is guaranteed not to be a path-traversal segment.
    """
    if value is None:
        return "_"
    sanitized = _UNSAFE_FILENAME_RE.sub("_", str(value))
    # Defend against components that resolve outside the output directory.
    sanitized = sanitized.replace("..", "_")
    if sanitized in ("", ".", ".."):
        return "_"
    return sanitized


def _is_actionable(result: Phase2Result) -> bool:
    return (
        result.status == "SUCCEEDED"
        and result.actionability is not None
        and result.actionability.decision == "ACTIONABLE"
    )


def _build_single_phase3(result: Phase2Result) -> Phase3Input:
    incident = result.incident_context
    event = incident.incident_event

    system_context = dict(incident.system_context or {})
    system_context["incident_fingerprint_sha256"] = result.fingerprint

    payload = {
        "schema_version": "1.0",
        "source": {
            "phase": "phase2",
            "event_id": result.event_id,
            "run_id": result.run_id,
            "correlation_id": result.correlation_id,
            "causation_id": result.causation_id,
        },
        "system_context": system_context,
        "incident_event": event.model_dump(mode="json"),
        "infrastructure_topology": incident.infrastructure_topology,
        "service_health_status": incident.service_health_status,
        "telemetry_evidence": incident.telemetry_evidence.model_dump(mode="json"),
        "injected_chaos_context": incident.injected_chaos_context,
        "phase2_context": {
            "actionability": result.actionability.model_dump(mode="json"),
            "retrieval": result.retrieval.model_dump(mode="json"),
            "historical_memory_evidence": [
                item.model_dump(mode="json") for item in result.matches
            ],
        },
        "agent_instruction": _AGENT_INSTRUCTION.format(target_service=event.target_service),
    }

    # Recursive redaction: secrets must never reach Phase 3, including nested
    # Phase 1 strings that ChromaDB redaction does not cover.
    redacted = redact_nested(payload)

    # Validate the sanitized object through the strict Phase3Input contract.
    return Phase3Input.model_validate(redacted)


def build_phase3_payloads(raw_payload: dict[str, Any]) -> list[Phase3Input]:
    """Convert a Phase2BatchOutput into one Phase3Input per actionable result.

    Selection rules:
    - Only results with status == "SUCCEEDED" AND actionability.decision == "ACTIONABLE".
    - Sorted deterministically by priority_score descending, incident_id ascending.
    - Every eligible incident produces exactly one Phase3Input.
    - SKIPPED / QUARANTINED / FAILED / NON_ACTIONABLE results are excluded.
    - Returns an empty list when there are no actionable incidents.
    """
    batch = Phase2BatchOutput.model_validate(raw_payload)

    selected = [item for item in batch.incidents if _is_actionable(item)]
    selected.sort(
        key=lambda item: (
            -item.incident_context.incident_event.priority_score,
            item.incident_context.incident_event.incident_id,
        )
    )

    return [_build_single_phase3(item) for item in selected]


def _write_atomic_json(target: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically: staging file -> fsync -> atomic rename."""
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_suffix(target.suffix + ".staging")
    content = json.dumps(data, indent=2).encode("utf-8")
    with staging.open("wb") as handle:
        handle.write(content)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            # fsync may be unsupported on some platforms/filesystems.
            pass
    staging.replace(target)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def write_phase3_payloads(
    source_path: str | Path,
    output_dir: str | Path,
) -> list[Path]:
    """Write one Phase3Input JSON file per actionable incident.

    Output filename: ``{run_id}__{incident_id}__{event_id}.json`` (sanitized).
    An atomic manifest is written to ``manifest.json`` in the output directory.

    Idempotency:
    - If the same event_id is processed again with identical content, it is a
      success (no rewrite).
    - If the content differs, an idempotency conflict is raised.
    """
    source = Path(source_path)
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    raw_payload = json.loads(source.read_text(encoding="utf-8"))
    payloads = build_phase3_payloads(raw_payload)

    written: list[Path] = []
    manifest_entries: list[dict[str, Any]] = []
    run_ids: set[str] = set()

    for payload in payloads:
        run_id = _sanitize_filename_component(payload.source.run_id)
        incident_id = _sanitize_filename_component(payload.incident_event.incident_id)
        event_id = _sanitize_filename_component(payload.source.event_id)

        filename = f"{run_id}__{incident_id}__{event_id}.json"
        target = output_root / filename

        data = payload.model_dump(mode="json")
        content = json.dumps(data, indent=2)

        if target.exists():
            existing = target.read_text(encoding="utf-8")
            if existing == content:
                # Idempotent success: identical content already present.
                pass
            else:
                raise FileExistsError(
                    f"Idempotency conflict for event_id={payload.source.event_id}: "
                    f"existing file {target} differs from new content."
                )
        else:
            _write_atomic_json(target, data)

        written.append(target)
        run_ids.add(payload.source.run_id)
        manifest_entries.append(
            {
                "event_id": payload.source.event_id,
                "incident_id": payload.incident_event.incident_id,
                "run_id": payload.source.run_id,
                "priority_score": payload.incident_event.priority_score,
                "path": filename,
                "sha256": _sha256_file(target),
            }
        )

    manifest = {
        "schema_version": "1.0",
        "source_event_type": "phase2.batch.completed",
        "run_ids": sorted(run_ids),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "payload_count": len(manifest_entries),
        "payloads": manifest_entries,
    }
    _write_atomic_json(output_root / "manifest.json", manifest)

    return written


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate strictly validated Phase 3 input documents from Phase 2 output"
    )
    parser.add_argument("--source", default="frontend_data/phase2_enriched.json")
    parser.add_argument("--output-dir", default="frontend_data/phase3_ready")
    parser.add_argument(
        "--allow-legacy-phase1",
        action="store_true",
        default=False,
        help="Allow legacy Phase 1 datasets as input (development only; off by default).",
    )
    args = parser.parse_args()

    try:
        source = Path(args.source)
        raw_payload = json.loads(source.read_text(encoding="utf-8"))

        event_type = raw_payload.get("event_type")
        phase = raw_payload.get("phase")
        schema_version = raw_payload.get("schema_version")

        accepted = (
            event_type == "phase2.batch.completed"
            and phase == "phase2"
            and schema_version == "1.0"
        )

        if not accepted:
            if args.allow_legacy_phase1:
                # Legacy adapter path: still raises because Phase 3 must not
                # bypass Phase 2 filtering. The flag exists for development
                # testing only.
                raise ValueError(
                    "Legacy Phase 1 input is only permitted with --allow-legacy-phase1; "
                    "production Phase 3 must not bypass Phase 2 filtering and retrieval."
                )
            raise ValueError(
                "Rejected input: expected event_type='phase2.batch.completed', "
                "phase='phase2', schema_version='1.0'. Legacy Phase 1 fallback is "
                "disabled by default to prevent bypassing Phase 2."
            )

        written = write_phase3_payloads(source, args.output_dir)

        batch = Phase2BatchOutput.model_validate(raw_payload)
        received = len(batch.incidents)
        actionable = len(written)
        skipped = received - actionable

        summary = {
            "status": "ok",
            "received_phase2_results": received,
            "actionable_phase3_payloads": actionable,
            "skipped_results": skipped,
            "output_directory": str(args.output_dir),
        }
        print(json.dumps(summary, indent=2))
        return 0
    except Exception as exc:  # noqa: BLE001 - surface any failure as non-zero exit
        print(json.dumps({"status": "error", "message": str(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())