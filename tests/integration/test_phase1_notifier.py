"""Tests for the Phase 1 notifier (stdlib-only, loopback safety)."""
from __future__ import annotations

import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from integration.laptop1 import phase1_notifier as notifier


class _Handler(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        _Handler.received.append((self.path, body))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args):
        pass


@pytest.fixture
def local_server():
    _Handler.received.clear()
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield port
    srv.shutdown()


def test_notify_success(local_server):
    ok = notifier.notify_phase1_dataset(
        dataset_path="/tmp/ds.json",
        generated_at="2026-08-18T02:53:37Z",
        git_sha="abc",
        incident_count=5,
        host="127.0.0.1",
        port=local_server,
    )
    assert ok is True
    assert len(_Handler.received) == 1
    path, body = _Handler.received[0]
    assert path == notifier.NOTIFY_PATH
    import json
    payload = json.loads(body)
    assert payload["event"] == "phase1_dataset_packaged"
    assert payload["incident_count"] == 5


def test_notify_refuses_non_loopback():
    # 8.8.8.8 is not loopback; the notifier must refuse to send.
    ok = notifier.notify_phase1_dataset(
        dataset_path="/tmp/ds.json",
        generated_at="2026-08-18T02:53:37Z",
        host="8.8.8.8",
        port=8102,
    )
    assert ok is False


def test_notify_handles_unreachable_gracefully():
    # Port 1 is essentially never listening; must return False, not raise.
    ok = notifier.notify_phase1_dataset(
        dataset_path="/tmp/ds.json",
        generated_at="2026-08-18T02:53:37Z",
        host="127.0.0.1",
        port=1,
    )
    assert ok is False


def test_build_payload_shape():
    data = notifier._build_payload("/tmp/ds.json", "2026-08-18T02:53:37Z", "sha", 3)
    import json
    p = json.loads(data)
    assert p["event"] == "phase1_dataset_packaged"
    assert p["notifier_version"] == notifier.NOTIFIER_VERSION
    assert p["incident_count"] == 3
