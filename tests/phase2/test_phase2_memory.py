import json
import unittest

from phase2.memory import IncidentMemory
from tests.phase2_test_support import (FakeCollection, FakeEmbedder,
                                       sample_dataset)


class MemoryTests(unittest.TestCase):
    def test_indexing_is_idempotent_and_retains_trace_metadata(self):
        collection = FakeCollection()
        memory = IncidentMemory(collection=collection, embedder=FakeEmbedder())
        first = memory.index_dataset(sample_dataset())
        second = memory.index_dataset(sample_dataset())
        self.assertEqual(first.indexed, 1)
        self.assertEqual(second.indexed, 1)
        self.assertEqual(collection.count(), 1)
        metadata = next(iter(collection.rows.values()))[2]
        self.assertEqual(json.loads(metadata["trace_ids_json"]), ["trace-1"])

    def test_blank_template_is_quarantined_without_rejecting_dataset(self):
        raw = sample_dataset()
        raw["incidents"][0]["telemetry_evidence"]["log_cluster_template"] = " "
        memory = IncidentMemory(collection=FakeCollection(), embedder=FakeEmbedder())
        report = memory.index_dataset(raw)
        self.assertEqual(report.received, 1)
        self.assertEqual(report.indexed, 0)
        self.assertEqual(report.skipped, 1)
        self.assertEqual(report.quarantined, 0)

    def test_exact_match_is_service_scoped_and_returns_similarity_one(self):
        memory = IncidentMemory(collection=FakeCollection(), embedder=FakeEmbedder())
        memory.index_dataset(sample_dataset())
        matches = memory.find_exact("auth-service", "Token secret missing")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].match_type, "exact")
        self.assertEqual(matches[0].similarity, 1.0)
        self.assertEqual(
            memory.find_exact("payment-service", "Token secret missing"), []
        )

    def test_semantic_search_applies_service_filter_and_threshold(self):
        memory = IncidentMemory(collection=FakeCollection(), embedder=FakeEmbedder())
        memory.index_dataset(sample_dataset())
        self.assertEqual(
            len(
                memory.search(
                    "secret unavailable",
                    target_service="auth-service",
                    min_similarity=0.8,
                )
            ),
            1,
        )
        self.assertEqual(
            memory.search("secret unavailable", target_service="payment-service"),
            [],
        )
        self.assertEqual(memory.search("secret unavailable", min_similarity=0.81), [])
