"""Measure Laptop 1 orchestration overhead without Phase 2 inference time."""
from __future__ import annotations

import json
import platform
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.laptop1.models import IntegrationRunRecord, RunStatus
from integration.laptop1.state_store import StateStore


def percentile(values: list[float], percentage: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentage / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def main() -> int:
    iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    if iterations < 20:
        raise ValueError("at least 20 iterations are required")
    samples: list[float] = []
    with tempfile.TemporaryDirectory(prefix="laptop1-latency-") as temp:
        store = StateStore(Path(temp) / "state.db")
        for index in range(iterations):
            started = time.perf_counter()
            run_id = f"latency_{index}"
            store.create_run(IntegrationRunRecord(
                run_id=run_id, dataset_generated_at="benchmark",
                status=RunStatus.PENDING))
            store.update_run(run_id, status=RunStatus.RUNNING)
            store.update_run(run_id, status=RunStatus.PHASE3_INPUTS_READY,
                             phase3_payload_count=1)
            assert store.get_run(run_id) is not None
            samples.append((time.perf_counter() - started) * 1000)
        store.close()

    report = {
        "schema_version": "1.0",
        "scope": "orchestration_sqlite_state_transitions_only",
        "excludes": ["embedding", "Chroma query", "Phase 2 policy", "Phase 3 model inference"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "iterations": iterations,
        "latency_ms": {
            "mean": statistics.mean(samples), "min": min(samples),
            "p50": percentile(samples, 50), "p95": percentile(samples, 95),
            "p99": percentile(samples, 99), "max": max(samples),
        },
        "environment": {"os": platform.platform(), "python": platform.python_version()},
    }
    output = ROOT / "runtime" / "artifacts" / "laptop1_latency_report.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_suffix(".json.staging")
    staging.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    staging.replace(output)
    print(json.dumps(report, indent=2))
    print(f"Report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
