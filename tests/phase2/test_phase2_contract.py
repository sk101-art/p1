import json
import tempfile
import unittest
from pathlib import Path

from llm_prompt_generator import build_phase3_payloads
from phase2.memory import IncidentMemory
from phase2.models import Phase1Dataset, Phase2BatchOutput
from phase2.normalization import incident_fingerprint, normalize_template
from phase2.pipeline import process_dataset, process_phase1_dataset
from tests.phase2_test_support import (FakeCollection, FakeEmbedder,
                                       sample_dataset)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "unified_master_dataset_sample.json"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _run_fixture_through_pipeline(raw):
    """Run a Phase1Dataset-shaped payload through Phase 2 to get a Phase2BatchOutput."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        memory = IncidentMemory(
            persist_dir=Path(tmp_dir) / "chroma_db", embedder=FakeEmbedder()
        )
        batch = process_phase1_dataset(raw, memory=memory)
        return batch.model_dump(mode="json")


class ContractTests(unittest.TestCase):
    def test_nested_phase1_contract_is_preserved_in_prompt(self):
        batch = _run_fixture_through_pipeline(load_fixture())
        payloads = build_phase3_payloads(batch)
        self.assertEqual(len(payloads), 1)
        payload = payloads[0]
        self.assertEqual(payload.incident_event.incident_id, "auth-service_1")
        self.assertEqual(payload.incident_event.target_service, "auth-service")
        self.assertTrue(
            payload.telemetry_evidence.log_cluster_template.startswith(
                "java.lang.NullPointerException"
            )
        )
        self.assertEqual(payload.system_context["current_health_score"], 95.0)
        self.assertEqual(
            payload.injected_chaos_context["active_infrastructure_mutations"], ""
        )

    def test_highest_priority_incident_is_selected_even_if_input_is_unsorted(self):
        raw = load_fixture()
        lower = json.loads(json.dumps(raw["incidents"][0]))
        lower["incident_event"].update(incident_id="auth-service_2", priority_score=1.0)
        higher = raw["incidents"][0]
        higher["incident_event"]["priority_score"] = 90.0
        raw["incidents"] = [lower, higher]
        batch = _run_fixture_through_pipeline(raw)
        payloads = build_phase3_payloads(batch)
        self.assertEqual(payloads[0].incident_event.incident_id, "auth-service_1")

    def test_blank_templates_do_not_generate_phase3_prompt(self):
        raw = load_fixture()
        raw["incidents"][0]["telemetry_evidence"]["log_cluster_template"] = "  "
        # The dataset remains parseable so one bad incident cannot poison the batch.
        Phase1Dataset.model_validate(raw)
        batch = _run_fixture_through_pipeline(raw)
        # A blank template is quarantined by the policy, so no actionable
        # incident survives to become a Phase 3 payload.
        payloads = build_phase3_payloads(batch)
        self.assertEqual(payloads, [])

    def test_fingerprint_uses_only_contract_key_and_normalizes_noise(self):
        a = incident_fingerprint(" Auth-Service ", "Timeout  0xABC")
        b = incident_fingerprint("auth-service", "timeout 0xdef")
        self.assertEqual(a, b)
        self.assertEqual(normalize_template(" A\n B "), "a b")

    def test_phase3_prompt_consumes_phase2_history_not_phase1_directly(self):
        memory = IncidentMemory(collection=FakeCollection(), embedder=FakeEmbedder())
        process_dataset(sample_dataset("auth-service_1"), memory)
        report, results = process_dataset(sample_dataset("auth-service_2"), memory)
        batch = Phase2BatchOutput(
            source_generated_at=report.dataset_generated_at,
            index_report=report,
            incidents=results,
        )
        payloads = build_phase3_payloads(batch.model_dump(mode="json"))
        self.assertEqual(len(payloads), 1)
        payload = payloads[0]
        self.assertEqual(payload.source.phase, "phase2")
        history = payload.phase2_context.historical_memory_evidence
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].incident_id, "auth-service_1")


if __name__ == "__main__":
    unittest.main()
