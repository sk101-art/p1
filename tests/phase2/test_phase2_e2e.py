"""End-to-end contract validation: Phase 1 -> Phase 2 -> strict Phase 3 boundary.

These tests verify the Phase 2 -> Phase 3 output boundary without modifying
Phase 1 behavior, retrieval logic, similarity calculations, ChromaDB indexing,
lifecycle operations, MCP worker logic, or CI configuration.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from llm_prompt_generator import (
    build_phase3_payloads,
    write_phase3_payloads,
)
from phase2.memory import IncidentMemory
from phase2.models import Phase2BatchOutput
from phase2.pipeline import process_phase1_dataset
from phase2.phase3_contract import Phase3Input

from tests.phase2_test_support import FakeEmbedder

GOLDEN_DATASET_PATH = Path(__file__).parent.parent / "fixtures" / "golden_phase1_dataset.json"
GOLDEN_PHASE3_PATH = Path(__file__).parent.parent / "fixtures" / "golden_phase3_input.json"

SECRETS = [
    "AKIA1234567890123456",
    "SuperSecretPass123",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.signature",
]


def _run_golden_batch() -> dict:
    raw = json.loads(GOLDEN_DATASET_PATH.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        memory = IncidentMemory(
            persist_dir=Path(tmp_dir) / "chroma_db", embedder=FakeEmbedder()
        )
        batch = process_phase1_dataset(raw, memory=memory)
        return batch.model_dump(mode="json")


def _golden_batch_file() -> Path:
    """Write the golden batch to a temp file and return its path."""
    path = Path(tempfile.gettempdir()) / "golden_batch_tmp.json"
    path.write_text(json.dumps(_run_golden_batch()))
    return path


class TestPhase2EndToEnd(unittest.TestCase):
    def test_golden_phase1_to_phase3_pipeline(self):
        self.assertTrue(GOLDEN_DATASET_PATH.exists(), f"Missing fixture: {GOLDEN_DATASET_PATH}")
        golden_raw = json.loads(GOLDEN_DATASET_PATH.read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            memory = IncidentMemory(persist_dir=Path(tmp_dir) / "chroma_db", embedder=FakeEmbedder())

            # Execute Phase 2 Pipeline
            batch_output = process_phase1_dataset(golden_raw, memory=memory)

            # Assert Phase 2 batch contract
            self.assertEqual(batch_output.event_type, "phase2.batch.completed")
            self.assertGreaterEqual(batch_output.index_report.received, 2)
            self.assertGreaterEqual(len(batch_output.incidents), 2)

            # Verify only actionable incidents are indexed
            actionable_count = 0
            for inc in batch_output.incidents:
                self.assertIsNotNone(inc.incident_context)
                self.assertIn(inc.actionability.decision, ["ACTIONABLE", "NON_ACTIONABLE", "QUARANTINED"])
                if inc.actionability.decision == "ACTIONABLE":
                    actionable_count += 1
                    self.assertTrue(inc.indexed)
                else:
                    self.assertFalse(inc.indexed)

            # Retrieve all documents to verify secrets are redacted in Chroma
            with memory._lock_context():
                results = memory.collection.get()
                for doc in results.get("documents", []):
                    for secret in SECRETS:
                        self.assertNotIn(secret, doc)
                for meta in results.get("metadatas", []):
                    for val in meta.values():
                        if isinstance(val, str):
                            for secret in SECRETS:
                                self.assertNotIn(secret, val)

            # Validate Phase2BatchOutput against Pydantic schema (extra="forbid")
            raw_output = batch_output.model_dump(mode="json")
            validated = Phase2BatchOutput.model_validate(raw_output)
            self.assertEqual(validated.event_type, "phase2.batch.completed")

            # Invoke real Phase 3 boundary generator
            payloads = build_phase3_payloads(raw_output)
            self.assertGreaterEqual(len(payloads), 1)
            for payload in payloads:
                self.assertIsInstance(payload, Phase3Input)
                self.assertEqual(payload.source.phase, "phase2")
                self.assertIsNotNone(payload.phase2_context.actionability)
                self.assertIsNotNone(payload.phase2_context.retrieval)
                self.assertIsInstance(payload.phase2_context.historical_memory_evidence, list)
                self.assertIsNotNone(payload.source.event_id)
                self.assertIsNotNone(payload.phase2_context.actionability.decision)
                self.assertIsNotNone(payload.phase2_context.retrieval.strategy)


class TestPhase3Selection(unittest.TestCase):
    def setUp(self):
        self.batch = _run_golden_batch()

    def test_one_actionable_produces_one_doc(self):
        # The golden dataset contains 2 actionable incidents (golden_inc_001,
        # golden_inc_002). Filter to a single actionable result and verify it
        # produces exactly one Phase3Input.
        batch = copy.deepcopy(self.batch)
        batch["incidents"] = [batch["incidents"][0]]
        payloads = build_phase3_payloads(batch)
        self.assertEqual(len(payloads), 1)

    def test_three_actionable_produces_three_docs(self):
        batch = copy.deepcopy(self.batch)
        base = batch["incidents"][0]
        for i in range(2):
            clone = copy.deepcopy(base)
            clone["incident_id"] = f"extra_inc_{i}"
            clone["incident_context"]["incident_event"]["incident_id"] = f"extra_inc_{i}"
            clone["incident_context"]["incident_event"]["priority_score"] = 50.0 - i
            clone["event_id"] = f"evt_extra_{i}"
            clone["source_event_id"] = f"evt_extra_src_{i}"
            batch["incidents"].append(clone)
        payloads = build_phase3_payloads(batch)
        self.assertEqual(len(payloads), 4)

    def test_highest_priority_first_and_lower_not_discarded(self):
        batch = copy.deepcopy(self.batch)
        base = batch["incidents"][0]
        clone = copy.deepcopy(base)
        clone["incident_id"] = "low_priority_inc"
        clone["incident_context"]["incident_event"]["incident_id"] = "low_priority_inc"
        clone["incident_context"]["incident_event"]["priority_score"] = 10.0
        clone["event_id"] = "evt_low"
        clone["source_event_id"] = "evt_low_src"
        batch["incidents"].append(clone)

        payloads = build_phase3_payloads(batch)
        self.assertEqual(len(payloads), 3)
        # Highest priority first
        self.assertGreaterEqual(
            payloads[0].incident_event.priority_score,
            payloads[1].incident_event.priority_score,
        )
        ids = {p.incident_event.incident_id for p in payloads}
        self.assertIn("low_priority_inc", ids)

    def test_skipped_excluded(self):
        batch = copy.deepcopy(self.batch)
        batch["incidents"][0]["status"] = "SKIPPED"
        payloads = build_phase3_payloads(batch)
        # golden_inc_001 skipped -> only golden_inc_002 remains
        self.assertEqual(len(payloads), 1)

    def test_quarantined_excluded(self):
        batch = copy.deepcopy(self.batch)
        batch["incidents"][0]["status"] = "QUARANTINED"
        payloads = build_phase3_payloads(batch)
        self.assertEqual(len(payloads), 1)

    def test_failed_excluded(self):
        batch = copy.deepcopy(self.batch)
        batch["incidents"][0]["status"] = "FAILED"
        payloads = build_phase3_payloads(batch)
        self.assertEqual(len(payloads), 1)

    def test_non_actionable_excluded(self):
        batch = copy.deepcopy(self.batch)
        batch["incidents"][0]["actionability"]["decision"] = "NON_ACTIONABLE"
        payloads = build_phase3_payloads(batch)
        self.assertEqual(len(payloads), 1)

    def test_empty_batch_produces_zero_docs(self):
        empty = copy.deepcopy(self.batch)
        empty["incidents"] = []
        payloads = build_phase3_payloads(empty)
        self.assertEqual(len(payloads), 0)

    def test_all_outputs_validate_phase3input(self):
        payloads = build_phase3_payloads(self.batch)
        self.assertGreater(len(payloads), 0)
        for payload in payloads:
            # Re-validate through the strict contract
            revalidated = Phase3Input.model_validate(payload.model_dump(mode="json"))
            self.assertEqual(revalidated.schema_version, "1.0")


class TestPhase3Boundary(unittest.TestCase):
    def setUp(self):
        self.batch = _run_golden_batch()

    def test_unknown_top_level_fields_rejected(self):
        batch = copy.deepcopy(self.batch)
        batch["unknown_field"] = "should-be-rejected"
        with self.assertRaises(Exception):
            build_phase3_payloads(batch)

    def test_missing_source_identifiers_rejected(self):
        batch = copy.deepcopy(self.batch)
        del batch["incidents"][0]["run_id"]
        with self.assertRaises(Exception):
            build_phase3_payloads(batch)

    def test_secrets_never_appear_in_output(self):
        payloads = build_phase3_payloads(self.batch)
        for payload in payloads:
            serialized = json.dumps(payload.model_dump(mode="json"))
            for secret in SECRETS:
                self.assertNotIn(secret, serialized)

    def test_duplicate_identical_event_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "phase3"
            written1 = write_phase3_payloads(_golden_batch_file(), out_dir)
            # Re-run with identical content: should not raise
            written2 = write_phase3_payloads(_golden_batch_file(), out_dir)
            self.assertEqual(len(written1), len(written2))

    def test_duplicate_event_id_differing_content_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "phase3"
            write_phase3_payloads(_golden_batch_file(), out_dir)
            # Mutate priority_score to force differing content
            batch = _run_golden_batch()
            batch["incidents"][0]["incident_context"]["incident_event"]["priority_score"] = 1.0
            batch_file = Path(tmp) / "mutated_batch.json"
            batch_file.write_text(json.dumps(batch))
            with self.assertRaises(FileExistsError):
                write_phase3_payloads(batch_file, out_dir)

    def test_filename_path_traversal_impossible(self):
        batch = copy.deepcopy(self.batch)
        batch["incidents"][0]["run_id"] = "../../etc"
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "phase3"
            batch_file = Path(tmp) / "traversal_batch.json"
            batch_file.write_text(json.dumps(batch))
            written = write_phase3_payloads(batch_file, out_dir)
            self.assertEqual(len(written), 2)
            # Every output file must stay inside the output directory
            for path in written:
                self.assertEqual(path.parent.resolve(), out_dir.resolve())
            # The run_id component in the filename must be sanitized
            for path in out_dir.iterdir():
                self.assertNotIn("/", path.name)
                self.assertNotIn("\\", path.name)
                self.assertNotIn("..", path.name)

    def test_legacy_phase1_fallback_rejected_by_default(self):
        # A Phase1Dataset-shaped payload must be rejected (not silently accepted)
        legacy = json.loads(GOLDEN_DATASET_PATH.read_text(encoding="utf-8"))
        with self.assertRaises(Exception):
            build_phase3_payloads(legacy)

    def test_canonical_phase2_batch_unchanged(self):
        batch = Phase2BatchOutput.model_validate(self.batch)
        dumped = batch.model_dump(mode="json")
        revalidated = Phase2BatchOutput.model_validate(dumped)
        self.assertEqual(batch.event_type, revalidated.event_type)
        self.assertEqual(len(batch.incidents), len(revalidated.incidents))


if __name__ == "__main__":
    unittest.main()
