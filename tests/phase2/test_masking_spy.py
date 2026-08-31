"""Unit test verifying that secrets are redacted before reaching the embedder via a Spy Embedder."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from phase2.memory import IncidentMemory
from phase2.normalization import redact_secrets


class SpyEmbedder:
    """Spy embedder recording raw input strings passed to encode()."""

    def __init__(self) -> None:
        self.recorded_inputs: list[list[str]] = []

    def encode(self, sentences: Any, **kwargs: Any) -> list[list[float]]:
        if isinstance(sentences, str):
            sentences = [sentences]
        self.recorded_inputs.append(list(sentences))
        return [[0.0] * 384 for _ in sentences]


class TestMaskingSpy(unittest.TestCase):
    def test_spy_embedder_verifies_redaction_before_encode(self):
        spy = SpyEmbedder()
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            memory = IncidentMemory(persist_dir=Path(d), embedder=spy)

            raw_dataset = {
                "generated_at": "2026-08-30T10:00:00Z",
                "incidents": [
                    {
                        "incident_event": {
                            "incident_id": "spy_inc_1",
                            "target_service": "auth-service",
                            "severity": "CRITICAL",
                            "priority_score": 90.0,
                            "occurrence_count": 1,
                        },
                        "telemetry_evidence": {
                            "log_cluster_template": "Login failed for user password=TopSecretPass123! token=eyJhbGciOiJIUzI1NiJ9.test.sig",
                            "log_samples": [],
                        },
                        "infrastructure_topology": {
                            "role": "auth-api",
                            "downstream_dependencies": [],
                        },
                        "service_health_status": {},
                        "injected_chaos_context": {},
                    }
                ],
            }

            memory.index_dataset(raw_dataset)
            memory.search("Login failed password=TopSecretPass123!")

        all_passed_texts = [
            text for call in spy.recorded_inputs for text in call
        ]

        self.assertTrue(len(all_passed_texts) >= 2)
        for text in all_passed_texts:
            self.assertNotIn("TopSecretPass123!", text)
            self.assertNotIn("eyJhbGciOiJIUzI1NiJ9", text)

    def test_redact_secrets_patterns(self):
        aws_key = "AKIAIOSFODNN7EXAMPLE"
        pem_key = "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC...\n-----END PRIVATE KEY-----"
        url_creds = "https://admin:SecretPass123@db.example.com:5432/main"
        jwt_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"

        text = f"Errors: aws={aws_key} url={url_creds} jwt={jwt_token} pem={pem_key}"
        redacted = redact_secrets(text)

        self.assertNotIn(aws_key, redacted)
        self.assertNotIn("SecretPass123", redacted)
        self.assertNotIn(jwt_token, redacted)
        self.assertNotIn("MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC", redacted)


if __name__ == "__main__":
    unittest.main()
