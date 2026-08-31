"""Phase 17: measure end-to-end run_once latency (p50/p95).

Warms the Phase 2 runtime once, then times repeated ``run_once`` calls against
a small synthetic dataset to capture steady-state latency percentiles.

Usage:
    python integration/laptop1/measure_latency.py [iterations] [num_incidents]
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

# Ensure the worktree root is importable when run directly.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integration.laptop1.controller import IntegrationController
from integration.laptop1.phase2_runtime import Phase2Runtime
from integration.laptop1.state_store import StateStore
from tests.integration.fixtures.fixture_loader import get_or_create


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def main() -> int:
    iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    num_incidents = int(sys.argv[2]) if len(sys.argv) > 2 else 10

    dataset_path = get_or_create(num_incidents=num_incidents)
    store = StateStore(":memory:")
    runtime = Phase2Runtime()
    ctrl = IntegrationController(runtime, store)

    print(f"Warming Phase 2 runtime (embedder + chromadb)...")
    runtime.warm()
    print("Runtime ready.")

    samples: list[float] = []
    for i in range(iterations):
        run_id = f"latency_{i}"
        t0 = time.perf_counter()
        rec = ctrl.run_once(dataset_path, run_id=run_id)
        dt = time.perf_counter() - t0
        samples.append(dt)
        print(f"  run {i}: status={rec.status.value} {dt*1000:.1f} ms")

    runtime.close()

    if samples:
        print("\n=== Latency summary (ms) ===")
        print(f"  iterations : {len(samples)}")
        print(f"  mean       : {statistics.mean(samples)*1000:.1f}")
        print(f"  min        : {min(samples)*1000:.1f}")
        print(f"  p50        : {_percentile(samples, 50)*1000:.1f}")
        print(f"  p95        : {_percentile(samples, 95)*1000:.1f}")
        print(f"  max        : {max(samples)*1000:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
