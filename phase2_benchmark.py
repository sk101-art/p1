"""High-scale performance benchmarking suite for Phase 2 incident memory (1,000-record index & concurrent load)."""

from __future__ import annotations

import concurrent.futures
import datetime
import hashlib
import json
import platform
import tempfile
import time
from pathlib import Path
from uuid import uuid4
from typing import cast

import psutil
from phase1_schema import (Incident, IncidentEvent, InfrastructureTopology,
                           InjectedChaosContext, LogSample, MetricsSnapshot,
                           ServiceHealthStatus, SystemContext,
                           TelemetryEvidence)
from phase2.config import VALIDATION_ARTIFACTS_DIR, DEFAULT_EMBED_MODEL
from phase2.memory import IncidentMemory
from phase2.models import Phase1Incident


def _make_incident(i: int, svc: str, err: str) -> Incident:
    """Build a schema-valid Incident for benchmarking."""
    return Incident(
        system_context=SystemContext(
            objective="benchmark",
            environment="benchmark",
            current_health_score=50.0,
            active_warnings=0,
        ),
        incident_event=IncidentEvent(
            incident_id=f"bench_inc_{i:04d}",
            target_service=svc,
            severity="CRITICAL" if i % 2 == 0 else "WARNING",
            priority_score=0.85,
            occurrence_count=1 + (i % 10),
        ),
        infrastructure_topology=InfrastructureTopology(
            role="backend",
            downstream_dependencies=["redis-cache", "postgres-db"],
            exposed_ports=["8080"],
        ),
        service_health_status=ServiceHealthStatus(
            docker_status="running",
            health_check="healthy",
            dependency_states={"redis-cache": "up", "postgres-db": "up"},
        ),
        telemetry_evidence=TelemetryEvidence(
            log_cluster_template=f"{err} (request_id=<VAR>)",
            log_samples=[
                LogSample(
                    timestamp="2026-08-30T00:00:00Z",
                    level="ERROR",
                    content=f"{err} (request_id=req_{i:06d})",
                    trace_id=f"trace_{i:06d}",
                )
            ],
            metrics_snapshot=[
                MetricsSnapshot(
                    timestamp="2026-08-30T00:00:00Z",
                    cpu_percent=75.0,
                    memory_usage_bytes=512 * 1024 * 1024,
                    memory_usage_percent=60.0,
                )
            ],
        ),
        injected_chaos_context=InjectedChaosContext(
            active_infrastructure_mutations="none"
        ),
    )


def generate_benchmark_dataset(count: int = 1000) -> list[Incident]:
    """Generate 1,000 synthetic incident records for high-scale indexing."""
    services = ["payment-service", "auth-service", "order-service", "inventory-service", "shipping-service"]
    errors = [
        "Connection refused to redis-cache:6379",
        "PostgreSQL connection timeout in connection pool",
        "Kafka consumer group rebalance deadlock",
        "Out of Memory: Killed process 1042 (java)",
        "HTTP 504 Gateway Timeout on upstream service",
    ]

    dataset = []
    for i in range(count):
        svc = services[i % len(services)]
        err = errors[i % len(errors)]
        dataset.append(_make_incident(i, svc, err))
    return dataset


def run_benchmark() -> dict:
    """Run 1,000-record index benchmark and concurrent search latency tests with RAM capture.

    Isolates store to a temporary directory to avoid mutating the default store.
    """
    random_seed = 42
    dataset = generate_benchmark_dataset(1000)
    
    # Calculate dataset hash
    dataset_json = json.dumps([inst.model_dump() for inst in dataset], sort_keys=True)
    dataset_hash = hashlib.sha256(dataset_json.encode("utf-8")).hexdigest()

    # Isolate benchmark DB
    with tempfile.TemporaryDirectory() as temporary_dir:
        bench_path = Path(temporary_dir) / "benchmark_chroma"
        col_name = f"phase2_benchmark_{uuid4().hex}"

        # 1. Measure model cold-start time (first initialization including embedder load)
        t0_cold = time.time()
        memory = IncidentMemory(persist_dir=bench_path, collection_name=col_name)
        # Force embedder initialization
        memory._embed(["warmup"])
        t1_cold = time.time()
        cold_start_time = t1_cold - t0_cold

        # 2. Indexing Benchmark
        t0_idx = time.time()
        indexed_count = memory.add_incidents_batch(cast(list[Phase1Incident], dataset))
        t1_idx = time.time()

        indexing_duration = t1_idx - t0_idx
        indexing_qps = indexed_count / indexing_duration if indexing_duration > 0 else 0.0

        # 3. Warm-query timing (single serial query after loading)
        t0_warm = time.time()
        memory.search("Database pool timeout", top_k=1)
        t1_warm = time.time()
        warm_query_time_ms = (t1_warm - t0_warm) * 1000.0

        # 4. Concurrent Search Benchmark
        # Note: IncidentMemory SQLite/Chroma backend store locking may serialize concurrent operations.
        # Concurrent callers will contend for the database write/read locks.
        queries = [
            ("Database connection pool exhausted under high traffic load", "payment-service"),
            ("Redis cache node out of memory error", "auth-service"),
            ("Kafka message consumer group rebalance timeout", "order-service"),
            ("HTTP 504 Gateway Timeout error", "inventory-service"),
        ] * 25  # 100 queries total

        latencies = []

        def single_query(q_tuple: tuple[str, str]) -> float:
            query_text, service = q_tuple
            start = time.time()
            memory.search(query_text, target_service=service, top_k=5)
            return (time.time() - start) * 1000.0  # ms

        t0_search = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(single_query, q) for q in queries]
            for f in concurrent.futures.as_completed(futures):
                latencies.append(f.result())
        t1_search = time.time()

        total_search_duration = t1_search - t0_search
        query_qps = len(queries) / total_search_duration if total_search_duration > 0 else 0.0

        latencies.sort()
        p50 = latencies[int(len(latencies) * 0.50)] if latencies else 0.0
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0.0
        p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0.0

        # Capture system RAM metrics
        process = psutil.Process()
        ram_mb = process.memory_info().rss / (1024.0 * 1024.0)

        # Gather platform & cpu details
        try:
            cpu_count = psutil.cpu_count(logical=True)
            cpu_freq = psutil.cpu_freq().max if psutil.cpu_freq() else 0.0
        except Exception:
            cpu_count = 0
            cpu_freq = 0.0

        report = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "model_name": DEFAULT_EMBED_MODEL,
            "random_seed": random_seed,
            "dataset_hash": dataset_hash,
            "dataset_size": indexed_count,
            "cold_start_time_seconds": round(cold_start_time, 4),
            "indexing_time_seconds": round(indexing_duration, 4),
            "indexing_throughput_qps": round(indexing_qps, 2),
            "warm_query_time_ms": round(warm_query_time_ms, 2),
            "concurrent_queries_count": len(queries),
            "concurrency_level": 4,
            "search_throughput_qps": round(query_qps, 2),
            "latency_p50_ms": round(p50, 2),
            "latency_p95_ms": round(p95, 2),
            "latency_p99_ms": round(p99, 2),
            "ram_usage_mb": round(ram_mb, 2),
            "environment": {
                "platform": platform.platform(),
                "python_version": platform.python_version(),
                "processor": platform.processor(),
                "cpu_count": cpu_count,
                "cpu_max_freq_mhz": cpu_freq,
            },
            "locking_notes": "IncidentMemory store locking may serialize concurrent read/write query executions.",
        }

        # Clean up memory
        memory.close()

    VALIDATION_ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    out_file = VALIDATION_ARTIFACTS_DIR / "phase2_benchmark_report.json"
    out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Benchmark report written to {out_file}")
    return report


if __name__ == "__main__":
    report = run_benchmark()
    print(json.dumps(report, indent=2))
