import importlib.util
import tempfile
import unittest
from pathlib import Path

from phase2.memory import IncidentMemory
from tests.phase2_test_support import FakeEmbedder, sample_dataset


@unittest.skipIf(
    importlib.util.find_spec("chromadb") is None,
    "requires the Phase 2 runtime dependency group",
)
class ChromaIntegrationTests(unittest.TestCase):
    def test_persistent_chroma_round_trip(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            path = Path(directory)
            first = IncidentMemory(persist_dir=path, embedder=FakeEmbedder())
            report = first.index_dataset(sample_dataset("auth-service_1"))
            self.assertEqual(report.indexed, 1)
            self.assertEqual(first.collection.count(), 1)

            # A new client proves the record reached disk, not a test double.
            reopened = IncidentMemory(persist_dir=path, embedder=FakeEmbedder())
            matches = reopened.find_exact("auth-service", "Token secret missing")
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0].incident_id, "auth-service_1")
            self.assertEqual(matches[0].trace_ids, ["trace-1"])

            semantic = reopened.search(
                "Token secret missing",
                target_service="auth-service",
                min_similarity=0.0,
            )
            self.assertEqual(len(semantic), 1)
