"""Phase 1 post-commit notifier (stdlib-only).

This module is intentionally restricted to the Python standard library so it
can be imported and invoked from ``package_ml_dataset.py`` without disturbing
the Phase 1 dependency surface. It performs a single, best-effort HTTP POST to
the local integration service after the canonical dataset has been atomically
written.

Safety invariants:
- Never raises into the caller's pipeline; all failures are logged and swallowed.
- Only ever targets 127.0.0.1 (configurable port, default 8102).
- Uses urllib.request exclusively (no third-party HTTP libraries).
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8102
NOTIFY_PATH = "/v1/notify/phase1-dataset"
NOTIFIER_VERSION = "1.0"
_TIMEOUT_SECONDS = 3.0


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_payload(dataset_path: str, generated_at: str, git_sha: str | None, incident_count: int) -> bytes:
    payload = {
        "event": "phase1_dataset_packaged",
        "dataset_path": dataset_path,
        "generated_at": generated_at,
        "git_sha": git_sha,
        "incident_count": incident_count,
        "notifier_version": NOTIFIER_VERSION,
        "emitted_at": _utcnow_iso(),
    }
    return json.dumps(payload).encode("utf-8")


def notify_phase1_dataset(
    dataset_path: str,
    generated_at: str,
    git_sha: str | None = None,
    incident_count: int = 0,
    *,
    host: str | None = None,
    port: int | None = None,
) -> bool:
    """Best-effort notify the integration service of a freshly packaged dataset.

    Returns True if the service acknowledged (2xx), False otherwise. Never
    raises.
    """
    host = host or os.getenv("LAPTOP1_NOTIFY_HOST", DEFAULT_HOST)
    try:
        port = int(port if port is not None else os.getenv("LAPTOP1_NOTIFY_PORT", str(DEFAULT_PORT)))
    except (TypeError, ValueError):
        port = DEFAULT_PORT

    # Hard safety guard: never notify a non-loopback host.
    try:
        info = socket.getaddrinfo(host, None)
        is_loopback = all(
            addr[4][0] in ("127.0.0.1", "::1", "localhost") or addr[4][0].startswith("127.")
            for addr in info
        )
    except socket.gaierror:
        is_loopback = host in ("127.0.0.1", "localhost", "::1") or host.startswith("127.")
    if not is_loopback:
        # Refuse to send dataset metadata off-box.
        return False

    url = f"http://{host}:{port}{NOTIFY_PATH}"
    data = _build_payload(dataset_path, generated_at, git_sha, incident_count)
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError:
        # Service responded but rejected; treat as handled.
        return False
    except (urllib.error.URLError, OSError, socket.timeout):
        # Service not running or unreachable; safe to ignore.
        return False


__all__ = ["notify_phase1_dataset"]
