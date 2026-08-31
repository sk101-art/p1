"""Dependency-free test doubles for the Phase 2 memory engine."""


class FakeVector(list):
    def tolist(self):
        return list(self)


class FakeEmbedder:
    def encode(self, texts, **kwargs):
        return [FakeVector([float(len(text)), 1.0]) for text in texts]


class FakeCollection:
    def __init__(self):
        self.rows = {}

    def count(self):
        return len(self.rows)

    def upsert(self, ids, embeddings, documents, metadatas):
        for key, vector, document, metadata in zip(
            ids, embeddings, documents, metadatas
        ):
            self.rows[key] = (vector, document, metadata)

    def get(self, where=None, include=None):
        selected = [
            (key, row)
            for key, row in self.rows.items()
            if not where
            or all(row[2].get(name) == value for name, value in where.items())
        ]
        return {
            "ids": [key for key, _ in selected],
            "documents": [row[1] for _, row in selected],
            "metadatas": [row[2] for _, row in selected],
        }

    def query(self, query_embeddings, n_results, include, where=None):
        selected = [
            (key, row)
            for key, row in self.rows.items()
            if not where
            or all(row[2].get(name) == value for name, value in where.items())
        ][:n_results]
        return {
            "ids": [[key for key, _ in selected]],
            "documents": [[row[1] for _, row in selected]],
            "metadatas": [[row[2] for _, row in selected]],
            "distances": [[0.2 for _ in selected]],
        }


def sample_dataset(
    incident_id="auth-service_1",
    target_service="auth-service",
    severity="HIGH",
    priority_score=65.5,
    template="Token secret missing",
    level="ERROR",
):
    return {
        "generated_at": "2026-08-15T10:00:00Z",
        "system_context": {},
        "metadata": {"dataset_version": "2.0.0"},
        "incidents": [
            {
                "incident_event": {
                    "incident_id": incident_id,
                    "target_service": target_service,
                    "priority_score": priority_score,
                    "severity": severity,
                    "occurrence_count": 5,
                },
                "telemetry_evidence": {
                    "log_cluster_template": template,
                    "log_samples": [
                        {
                            "timestamp": "2026-08-15T10:00:00Z",
                            "level": level,
                            "content": template,
                            "trace_id": "trace-1",
                        }
                    ],
                    "metrics_snapshot": [],
                },
                "infrastructure_topology": {
                    "role": "authentication-service",
                    "downstream_dependencies": ["redis"],
                },
            }
        ],
    }
