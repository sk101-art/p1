import unittest

from phase2.memory import IncidentMemory
from phase2.pipeline import process_dataset
from tests.phase2_test_support import (FakeCollection, FakeEmbedder,
                                       sample_dataset)


class PipelineTests(unittest.TestCase):
    def test_new_incident_does_not_match_itself(self):
        memory = IncidentMemory(collection=FakeCollection(), embedder=FakeEmbedder())
        report, results = process_dataset(sample_dataset(), memory)
        self.assertEqual(report.indexed, 1)
        self.assertEqual(results[0].matches, [])

    def test_second_distinct_incident_can_reuse_exact_history(self):
        memory = IncidentMemory(collection=FakeCollection(), embedder=FakeEmbedder())
        process_dataset(sample_dataset("auth-service_1"), memory)
        _, results = process_dataset(sample_dataset("auth-service_2"), memory)
        self.assertEqual(len(results[0].matches), 1)
        self.assertEqual(results[0].matches[0].incident_id, "auth-service_1")
        self.assertEqual(results[0].matches[0].match_type, "exact")

    def test_cross_service_semantic_history_is_labeled(self):
        memory = IncidentMemory(collection=FakeCollection(), embedder=FakeEmbedder())
        process_dataset(
            sample_dataset("payment-service_1", target_service="payment-service"),
            memory,
        )
        _, results = process_dataset(
            sample_dataset("auth-service_2", target_service="auth-service"),
            memory,
            min_similarity=0.8,
        )
        self.assertEqual(len(results[0].matches), 1)
        self.assertEqual(results[0].matches[0].retrieval_scope, "cross_service")
        self.assertEqual(results[0].retrieval.strategy, "SEMANTIC")
