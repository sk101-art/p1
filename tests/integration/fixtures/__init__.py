"""
Synthetic Phase 1 dataset fixture for integration tests.
Small, fresh-dated payload with controlled incident mix for deterministic
Phase 2 processing.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

FIXTURE_DIR = Path(__file__).parent

_SAMPLE_TEMPLATES = [
    "java.lang.NullPointerException: Token secret missing",
    "redis.clients.jedis.exceptions.JedisConnectionException: Failed to connect",
    "org.springframework.dao.DataIntegrityViolationException: Duplicate key",
    "java.net.ConnectException: Connection refused (postgres-db:5432)",
    "com.rabbitmq.client.ShutdownSignalException: channel error",
    "java.lang.OutOfMemoryError: Java heap space",
    "java.io.IOException: Broken pipe",
    "java.util.concurrent.TimeoutException: Request timed out",
]

_SAMPLE_LOG_CONTENTS = [
    "at com.ecommerce.auth.AuthService.login(AuthService.java:42)",
    "at com.ecommerce.order.OrderService.checkout(OrderService.java:87)",
    "at com.ecommerce.payment.PaymentService.process(PaymentService.java:33)",
    "at com.ecommerce.gateway.GatewayFilter.doFilter(GatewayFilter.java:56)",
    "at com.ecommerce.auth.JwtValidator.validate(JwtValidator.java:19)",
    "at com.ecommerce.inventory.InventoryService.reserve(InventoryService.java:71)",
    "at com.ecommerce.notification.NotificationService.send(NotificationService.java:44)",
    "at com.ecommerce.common.db.LazyConnection.connect(LazyConnection.java:28)",
]


def _build_incident(i: int, template_idx: int) -> dict:
    target_services = [
        "auth-service", "auth-service", "auth-service", "auth-service",
        "order-service", "order-service", "order-service",
        "payment-service", "payment-service",
        "api-gateway", "api-gateway",
        "postgres-db",
        "rabbitmq",
        "redis",
    ]
    severities = ["CRITICAL", "HIGH", "HIGH", "MEDIUM", "HIGH", "MEDIUM", "LOW",
                  "HIGH", "MEDIUM", "CRITICAL", "MEDIUM", "HIGH", "MEDIUM", "LOW"]
    ts = target_services[i % len(target_services)]
    sv = severities[i % len(severities)]
    log_tpl = _SAMPLE_TEMPLATES[template_idx % len(_SAMPLE_TEMPLATES)]
    log_content = _SAMPLE_LOG_CONTENTS[template_idx % len(_SAMPLE_LOG_CONTENTS)]

    return {
        "system_context": {
            "objective": "Perform automated Multi-Agent Root Cause Analysis",
            "environment": "Dockerized Microservices (Java/Spring Boot, PostgreSQL, Redis, RabbitMQ, OpenTelemetry)",
            "current_health_score": max(30, 95.0 - i * 5),
            "active_warnings": i + 1,
        },
        "incident_event": {
            "incident_id": f"{ts}_{i}",
            "target_service": ts,
            "priority_score": round(130.0 - i * 2.5, 2),
            "severity": sv,
            "occurrence_count": max(1, 10 - i),
        },
        "infrastructure_topology": {
            "role": f"{ts}-service",
            "downstream_dependencies": ["postgres-db", "redis"] if ts in ("auth-service", "order-service", "payment-service") else ["postgres-db"],
            "exposed_ports": ["8081:8081"] if ts == "auth-service" else ["8082:8082"] if ts == "order-service" else ["8083:8083"],
        },
        "service_health_status": {
            "docker_status": "running",
            "health_check": "healthy" if i % 3 != 0 else "degraded",
            "dependency_states": {"postgres-db": "healthy", "redis": "healthy"},
        },
        "telemetry_evidence": {
            "log_cluster_template": log_tpl,
            "log_samples": [
                {
                    "timestamp": "2026-08-18T02:53:37Z",
                    "level": "ERROR",
                    "content": f"{log_tpl}\n\t{log_content}",
                    "trace_id": f"trace_{i:04x}",
                    "span_id": f"span_{i:04x}",
                }
            ],
            "metrics_snapshot": [
                {
                    "timestamp": "2026-08-18T02:53:37Z",
                    "cpu_percent": 12.5 + i * 1.5,
                    "memory_usage_bytes": 104857600 + i * 1024,
                    "memory_usage_percent": 25.0 + i * 0.5,
                }
            ],
        },
        "injected_chaos_context": {
            "active_infrastructure_mutations": "" if i % 2 == 0 else "network-partition"
        },
    }


def generate_fixture(num_incidents: int = 10) -> dict:
    """Produce a synthetic Phase 1 dataset dict with *num_incidents* entries."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "generated_at": now,
        "metadata": {
            "dataset_version": "2.0.0",
            "processor_version": 2,
            "git_sha": "0000000000000000000000000000000000000000",
            "source_files": {"status.json": 3, "processed_incidents.json": num_incidents},
            "schema_version": "1.0",
        },
        "system_context": {
            "objective": "Perform automated Multi-Agent Root Cause Analysis",
            "environment": "Dockerized Microservices (Java/Spring Boot, PostgreSQL, Redis, RabbitMQ, OpenTelemetry)",
        },
        "incidents": [_build_incident(i, i) for i in range(num_incidents)],
    }


def write_fixture(path: str | Path, num_incidents: int = 10) -> Path:
    """Write a synthetic fixture to *path* and return it."""
    path = Path(path)
    data = generate_fixture(num_incidents)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


# Default fixture file path
DEFAULT_FIXTURE = FIXTURE_DIR / "phase1_synthetic_dataset.json"

if __name__ == "__main__":
    p = write_fixture(DEFAULT_FIXTURE, num_incidents=10)
    print(f"Wrote synthetic fixture: {p} ({p.stat().st_size} bytes)")