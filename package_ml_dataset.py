import os
import yaml
import time
import gc
import subprocess
from datetime import datetime, timezone
from utils import (
    atomic_write_json,
    read_json_file,
    parse_iso_dt,
    get_logger,
    migrate_legacy_chaos_history,
    correlate_chaos_event,
    project_path
)

# Integration hook (Phase 6): stdlib-only notifier to the local laptop1
# Phase1<->Phase2 bridge. Imported lazily-guarded so Phase 1 never depends on
# the integration package at import time.
try:
    from integration.laptop1.phase1_notifier import notify_phase1_dataset
except Exception:  # pragma: no cover - integration package optional at runtime
    notify_phase1_dataset = None
from phase1_schema import (
    UnifiedMasterDataset,
    DatasetMeta,
    SystemContext,
    Incident,
    IncidentEvent,
    InfrastructureTopology,
    ServiceHealthStatus,
    TelemetryEvidence,
    InjectedChaosContext,
    LogSample,
    MetricsSnapshot
)

logger = get_logger("package_ml_dataset")

COMPOSE_FILE = project_path("docker-compose.yml")
STATUS_FILE = project_path("frontend_data", "status.json")
TIME_SERIES_FILE = project_path("frontend_data", "time_series.json")
PROCESSED_INCIDENTS_FILE = project_path("frontend_data", "processed_incidents.json")
CHAOS_HISTORY_FILE = project_path("frontend_data", "chaos_history.json")
OUTPUT_DATASET_FILE = project_path("frontend_data", "unified_master_dataset.json")

def parse_docker_compose_topology():
    if not os.path.exists(COMPOSE_FILE):
        return {}

    with open(COMPOSE_FILE, "r", encoding="utf-8") as f:
        compose_cfg = yaml.safe_load(f) or {}

    services = compose_cfg.get("services", {})
    known_services = set(services.keys())
    topology = {}

    for name, s_cfg in services.items():
        # 1. Role extraction from label ara.topology.role
        labels = s_cfg.get("labels", [])
        role = name
        if isinstance(labels, list):
            for lbl in labels:
                if isinstance(lbl, str) and lbl.startswith("ara.topology.role="):
                    role = lbl.split("=", 1)[1]
        elif isinstance(labels, dict):
            role = labels.get("ara.topology.role", name)

        # 2. Exposed ports
        exposed_ports = []
        raw_ports = s_cfg.get("ports", [])
        for p in raw_ports:
            if isinstance(p, str):
                exposed_ports.append(p)
            elif isinstance(p, dict):
                published = p.get("published")
                target = p.get("target")
                if published and target:
                    exposed_ports.append(f"{published}:{target}")
                elif target:
                    exposed_ports.append(str(target))

        # 3. Downstream dependencies
        deps = set()
        depends_on = s_cfg.get("depends_on", [])
        if isinstance(depends_on, list):
            for d in depends_on:
                if d in known_services:
                    deps.add(d)
        elif isinstance(depends_on, dict):
            for d in depends_on.keys():
                if d in known_services:
                    deps.add(d)

        # Inspect environment variables for cross-service links
        env_vars = s_cfg.get("environment", {})
        env_list = []
        if isinstance(env_vars, dict):
            env_list = [f"{k}={v}" for k, v in env_vars.items()]
        elif isinstance(env_vars, list):
            env_list = env_vars

        for env_entry in env_list:
            if isinstance(env_entry, str):
                for other_svc in known_services:
                    if other_svc != name and other_svc in env_entry:
                        deps.add(other_svc)

        topology[name] = {
            "role": role,
            "downstream_dependencies": sorted(list(deps)),
            "exposed_ports": exposed_ports
        }

    return topology

def package_dataset():
    start_time = time.time()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    logger.info("=== [Auto-SRE Phase 1] Packaging Unified Master Dataset for ML/LLM ===")

    # 1. Parse topology from docker-compose.yml
    topology_map = parse_docker_compose_topology()

    # 2. Read live data files
    status_data = read_json_file(STATUS_FILE, {})
    processed_incidents_data = read_json_file(PROCESSED_INCIDENTS_FILE, {})
    migrated = migrate_legacy_chaos_history(CHAOS_HISTORY_FILE)
    if migrated:
        logger.info(f"Migrated {migrated} legacy chaos history entries to unified schema")
    chaos_history_data = read_json_file(CHAOS_HISTORY_FILE, [])

    system_health_score = status_data.get("system_health_score", 100.0)
    active_warnings_count = status_data.get("active_warnings", 0)

    # Build status mapping for services
    status_services_map = {}
    for s in status_data.get("services", []):
        status_services_map[s.get("name")] = s

    # 3. Construct system context
    system_context = SystemContext(
        objective="Perform automated Multi-Agent Root Cause Analysis (RCA) and recommend corrective actions.",
        environment="Dockerized Microservices (Java/Spring Boot, PostgreSQL, Redis, RabbitMQ, OpenTelemetry)",
        current_health_score=system_health_score,
        active_warnings=active_warnings_count
    )

    incidents_raw = processed_incidents_data.get("incidents", [])
    packaged_incidents = []

    for inc in incidents_raw:
        ie_data = inc.get("incident_event", {})
        target_service = ie_data.get("target_service", "unknown")

        # Incident Event
        incident_event = IncidentEvent(
            incident_id=ie_data.get("incident_id"),
            target_service=target_service,
            priority_score=float(ie_data.get("priority_score", 0.0)),
            severity=ie_data.get("severity", "LOW"),
            occurrence_count=int(ie_data.get("occurrence_count", 1))
        )

        # Dynamic Infrastructure Topology
        topo_data = topology_map.get(target_service, {
            "role": target_service,
            "downstream_dependencies": [],
            "exposed_ports": []
        })
        infrastructure_topology = InfrastructureTopology(
            role=topo_data.get("role", target_service),
            downstream_dependencies=topo_data.get("downstream_dependencies", []),
            exposed_ports=topo_data.get("exposed_ports", [])
        )

        # Service Health Status
        sh_data = inc.get("service_health_status", {})
        s_stat = status_services_map.get(target_service, {})
        service_health_status = ServiceHealthStatus(
            docker_status=sh_data.get("docker_status") or s_stat.get("docker_status", "running"),
            health_check=sh_data.get("health_check") or s_stat.get("health_check", "healthy"),
            dependency_states=sh_data.get("dependency_states") or s_stat.get("dependency_states", {})
        )

        # Telemetry Evidence
        te_data = inc.get("telemetry_evidence", {})
        log_samples_raw = te_data.get("log_samples", [])
        metrics_snapshot_raw = te_data.get("metrics_snapshot", [])

        log_samples = [
            LogSample(
                timestamp=ls.get("timestamp", now_iso),
                level=ls.get("level", "INFO"),
                content=ls.get("content", ""),
                trace_id=ls.get("trace_id"),
                span_id=ls.get("span_id")
            )
            for ls in log_samples_raw[:5]
        ]

        metrics_snapshot = [
            MetricsSnapshot(
                timestamp=ms.get("timestamp", now_iso),
                cpu_percent=float(ms.get("cpu_percent", 0.0)),
                memory_usage_bytes=int(ms.get("memory_usage_bytes", 0)),
                memory_usage_percent=float(ms.get("memory_usage_percent", 0.0))
            )
            for ms in metrics_snapshot_raw[:3]
        ]

        telemetry_evidence = TelemetryEvidence(
            log_cluster_template=te_data.get("log_cluster_template", ""),
            log_samples=log_samples,
            metrics_snapshot=metrics_snapshot
        )

        # Injected Chaos Context (FIX M3: shared correlate_chaos_event using earliest/latest ts)
        earliest_ts_str = inc.get("earliest_ts")
        latest_ts_str = inc.get("latest_ts")
        if earliest_ts_str and latest_ts_str:
            cluster_start_dt = parse_iso_dt(earliest_ts_str)
            cluster_end_dt = parse_iso_dt(latest_ts_str)
        else:
            sample_dts = [parse_iso_dt(ls.timestamp) for ls in log_samples] if log_samples else [parse_iso_dt(now_iso)]
            cluster_start_dt = min(sample_dts)
            cluster_end_dt = max(sample_dts)

        chaos_mutation_desc = correlate_chaos_event(
            cluster_start_dt=cluster_start_dt,
            cluster_end_dt=cluster_end_dt,
            container_name=target_service,
            chaos_history_data=chaos_history_data
        )

        injected_chaos_context = InjectedChaosContext(
            active_infrastructure_mutations=chaos_mutation_desc
        )

        incident_model = Incident(
            system_context=system_context,
            incident_event=incident_event,
            infrastructure_topology=infrastructure_topology,
            service_health_status=service_health_status,
            telemetry_evidence=telemetry_evidence,
            injected_chaos_context=injected_chaos_context
        )
        packaged_incidents.append(incident_model)

    # 4. Construct DatasetMeta lineage
    git_sha = "unknown"
    try:
        git_sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or "unknown"
    except Exception:
        pass

    dataset_meta = DatasetMeta(
        dataset_version="2.0.0",
        processor_version=2,
        git_sha=git_sha,
        source_files={
            "status.json": len(status_data.get("services", [])),
            "processed_incidents.json": len(incidents_raw),
            "chaos_history.json": len(chaos_history_data)
        },
        schema_version="1.0"
    )

    # Build Master Dataset model
    master_dataset = UnifiedMasterDataset(
        generated_at=now_iso,
        metadata=dataset_meta,
        system_context=system_context,
        incidents=packaged_incidents
    )

    # 5. Strict Schema Validation with Pydantic
    try:
        serialized_payload = master_dataset.model_dump()
        UnifiedMasterDataset.model_validate(serialized_payload)
    except Exception as validation_err:
        logger.error(f"FATAL: Master dataset validation failed: {validation_err}")
        return

    # 6. Atomic write
    atomic_write_json(OUTPUT_DATASET_FILE, serialized_payload)
    gc.collect()

    # 6b. Integration hook (Phase 6): notify the local laptop1 bridge that a
    # fresh canonical dataset is available. Best-effort; never blocks or fails
    # the Phase 1 packaging contract.
    if notify_phase1_dataset is not None:
        try:
            notify_phase1_dataset(
                dataset_path=OUTPUT_DATASET_FILE,
                generated_at=now_iso,
                git_sha=git_sha if git_sha != "unknown" else None,
                incident_count=len(packaged_incidents),
            )
        except Exception as _notify_err:  # pragma: no cover - defensive
            logger.warning(f"Integration notify skipped: {_notify_err}")

    elapsed = time.time() - start_time
    severity_counts = {}
    for inc in packaged_incidents:
        sev = inc.incident_event.severity
        severity_counts[sev] = severity_counts.get(sev, 0) + 1

    logger.info(f"SUCCESS! Master dataset packaged and validated in {elapsed:.3f}s. Total Incidents: {len(packaged_incidents)}, Breakdown: {severity_counts}")

if __name__ == "__main__":
    package_dataset()
