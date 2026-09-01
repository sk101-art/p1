"""Automated validation and regression test for Laptop 1 pipeline output artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

import pytest


def test_pipeline_live_artifacts_and_boundary():
    state_dir = Path("runtime/artifacts/runs")
    runs = sorted(
        [d for d in state_dir.iterdir() if d.is_dir() and d.name.startswith("run_")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    assert runs, "No pipeline runs found under runtime/artifacts/runs"
    run_dir = runs[0]

    # 1. Artifact structure
    p1_file = run_dir / "phase1" / "dataset.json"
    p2_file = run_dir / "phase2" / "phase2_enriched.json"
    p2_manifest_file = run_dir / "phase2" / "manifest.json"
    p3_manifest_file = run_dir / "phase3_ready" / "manifest.json"

    assert p1_file.is_file(), f"Missing {p1_file}"
    assert p2_file.is_file(), f"Missing {p2_file}"
    assert p2_manifest_file.is_file(), f"Missing {p2_manifest_file}"
    assert p3_manifest_file.is_file(), f"Missing {p3_manifest_file}"

    p2_data = json.loads(p2_file.read_text(encoding="utf-8"))
    p2_manifest = json.loads(p2_manifest_file.read_text(encoding="utf-8"))
    p3_manifest = json.loads(p3_manifest_file.read_text(encoding="utf-8"))

    # 2. Phase 2 SHA-256 Checksum
    actual_p2_sha = hashlib.sha256(p2_file.read_bytes()).hexdigest().lower()
    expected_p2_sha = p2_manifest["sha256"].lower()
    assert actual_p2_sha == expected_p2_sha, f"Phase 2 SHA256 mismatch: {actual_p2_sha} != {expected_p2_sha}"

    # 3. Phase 3 Payloads Checksum & Validity
    payloads = p3_manifest.get("payloads", [])
    assert p3_manifest.get("payload_count") == len(payloads), "Phase 3 manifest payload_count mismatch"
    assert len(payloads) > 0, "Expected non-zero Phase 3 payloads"

    p3_event_ids = set()
    for entry in payloads:
        payload_path = run_dir / "phase3_ready" / entry["path"]
        assert payload_path.is_file(), f"Missing Phase 3 payload: {payload_path}"
        actual_sha = hashlib.sha256(payload_path.read_bytes()).hexdigest().lower()
        expected_sha = entry["sha256"].lower()
        assert actual_sha == expected_sha, f"Checksum mismatch for payload {entry['path']}"
        payload_json = json.loads(payload_path.read_text(encoding="utf-8"))
        eid = payload_json.get("source", {}).get("event_id") or payload_json.get("event_id")
        assert eid, f"Missing event_id in payload {entry['path']}"
        assert eid not in p3_event_ids, f"Duplicate event_id in Phase 3 payloads: {eid}"
        p3_event_ids.add(eid)

    # 4. Phase 2 -> Phase 3 Selection Mapping
    p2_incidents = p2_data.get("incidents", [])
    actionable_count = 0
    non_actionable_count = 0
    quarantined_count = 0
    skipped_count = 0
    error_count = 0
    actionable_event_ids = set()

    for inc in p2_incidents:
        decision = inc.get("actionability", {}).get("decision")
        eid = inc.get("event_id")
        if decision in ("ACTIONABLE", "PROVISIONAL"):
            actionable_count += 1
            actionable_event_ids.add(eid)
        elif decision in ("NON_ACTIONABLE", "BENIGN"):
            non_actionable_count += 1
        elif decision == "QUARANTINED":
            quarantined_count += 1
        elif decision == "SKIPPED":
            skipped_count += 1
        else:
            error_count += 1

    assert actionable_count == len(payloads), f"Actionable count ({actionable_count}) != payload count ({len(payloads)})"
    assert p3_event_ids == actionable_event_ids, "Phase 3 event IDs must match Phase 2 actionable event IDs exactly"
    assert non_actionable_count > 0, "Expected non-actionable incidents in test dataset"
    assert quarantined_count == 0
    assert error_count == 0

    # 5. Secret Leakage Scan
    secret_patterns = {
        "AWS Access Key": re.compile(r"AKIA[0-9A-Z]{16}"),
        "JWT Token": re.compile(r"eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}"),
        "Password Plaintext": re.compile(r"(?:password|passwd|pwd)\s*[=:]\s*['\"]?(?!<[^>]+>)[a-zA-Z0-9@#$%^&*!_+=-]{8,}['\"]?", re.IGNORECASE),
        "API Key assignment": re.compile(r"api[_-]?key\s*[=:]\s*['\"]?(?!<[^>]+>)[a-zA-Z0-9_-]{16,}['\"]?", re.IGNORECASE),
        "URL Credentials": re.compile(r"https?://[^:\s]+:[^@\s]+@[^\s]+"),
        "Database DSN Credentials": re.compile(r"(?:postgres|postgresql|mysql|mongodb|redis)://[^:\s]+:[^@\s]+@[^\s]+"),
        "PEM Private Key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    }

    files_to_scan = [p1_file, p2_file] + [run_dir / "phase3_ready" / entry["path"] for entry in payloads]
    leaks = []
    for file_path in files_to_scan:
        content = file_path.read_text(encoding="utf-8")
        for category, pattern in secret_patterns.items():
            matches = pattern.findall(content)
            # Exclude standard template / redaction markers
            real = [m for m in matches if not any(tag in m for tag in ["<MASKED>", "<REDACTED>", "<VAR>", "<AWS_KEY>", "<PASSWORD>", "<BEARER_TOKEN>", "<TOKEN>", "<HEX>", "<SECRET>", "<PARAM>", "<TIMESTAMP>", "<THREAD>", "<LINE>"])]
            if real:
                leaks.append((file_path.name, category, len(real)))

    assert not leaks, f"Secret leakage detected in artifacts: {leaks}"

    # 6. Latency Report
    latency_report_path = Path("runtime/artifacts/laptop1_latency_report.json")
    assert latency_report_path.is_file(), f"Missing {latency_report_path}"
    latency_data = json.loads(latency_report_path.read_text(encoding="utf-8"))
    assert latency_data.get("scope") == "orchestration_sqlite_state_transitions_only"
    assert "latency_ms" in latency_data
    assert "p50" in latency_data["latency_ms"]
    assert "p95" in latency_data["latency_ms"]
    assert "p99" in latency_data["latency_ms"]
