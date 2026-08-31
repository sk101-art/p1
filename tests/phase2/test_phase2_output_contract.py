import unittest

from pydantic import ValidationError

from phase2.filtering import ActionabilityPolicy
from phase2.memory import IncidentMemory
from phase2.models import Phase1Dataset, Phase2InputEnvelope, Phase2Result
from phase2.pipeline import build_input_envelopes, process_dataset
from tests.phase2_test_support import (FakeCollection, FakeEmbedder,
                                       sample_dataset)


class OutputContractTests(unittest.TestCase):
    def test_envelope_ids_are_deterministic_for_duplicate_delivery(self):
        dataset = Phase1Dataset.model_validate(sample_dataset())
        first = build_input_envelopes(dataset)[0]
        second = build_input_envelopes(dataset)[0]
        self.assertEqual(first.event_id, second.event_id)
        self.assertEqual(first.idempotency_key, second.idempotency_key)
        self.assertEqual(first.run_id, second.run_id)

    def test_phase2_result_contains_complete_forward_context(self):
        memory = IncidentMemory(collection=FakeCollection(), embedder=FakeEmbedder())
        report, results = process_dataset(sample_dataset(), memory)
        result = results[0]
        serialized = result.model_dump(mode="json")
        Phase2Result.model_validate(serialized)
        self.assertEqual(report.indexed, 1)
        self.assertEqual(result.status, "SUCCEEDED")
        self.assertEqual(
            result.incident_context.incident_event.target_service, "auth-service"
        )
        self.assertEqual(result.retrieval.strategy, "NONE")
        self.assertTrue(result.correlation_id.startswith("trace_"))
        self.assertEqual(result.causation_id, result.source_event_id)
        self.assertTrue(result.event_id.startswith("evt_"))

    def test_phase2_envelope_rejects_undeclared_fields(self):
        envelope = build_input_envelopes(
            Phase1Dataset.model_validate(sample_dataset())
        )[0].model_dump()
        envelope["unexpected"] = "unsafe contract drift"
        with self.assertRaises(ValidationError):
            Phase2InputEnvelope.model_validate(envelope)

    def test_non_actionable_incident_is_not_embedded(self):
        raw = sample_dataset(
            severity="LOW",
            priority_score=1.0,
            template="Health check passed",
            level="INFO",
        )
        raw["incidents"][0]["incident_event"]["occurrence_count"] = 1
        collection = FakeCollection()
        report, results = process_dataset(
            raw,
            IncidentMemory(collection=collection, embedder=FakeEmbedder()),
            policy=ActionabilityPolicy(threshold=0.35),
        )
        self.assertEqual(report.indexed, 0)
        self.assertEqual(report.skipped, 1)
        self.assertEqual(collection.count(), 0)
        self.assertEqual(results[0].status, "SKIPPED")
        self.assertEqual(results[0].retrieval.strategy, "NOT_RUN")
