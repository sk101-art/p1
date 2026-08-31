import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path
import json

from phase2.pipeline import main, Phase2BatchOutput
from phase2.memory import IncidentMemory
from tests.phase2_test_support import FakeEmbedder

class PipelineMainTests(unittest.TestCase):
    @patch("phase2.pipeline.argparse.ArgumentParser.parse_args")
    @patch("phase2.pipeline.Path.write_text")
    @patch("phase2.pipeline.Path.replace")
    @patch("phase2.pipeline.Path.mkdir")
    @patch("builtins.print")
    def test_pipeline_main(self, mock_print, mock_mkdir, mock_replace, mock_write, mock_args):
        # We need a dummy raw dataset.
        dataset_path = Path("frontend_data/unified_master_dataset.json")
        if not dataset_path.exists():
            self.skipTest("Missing master dataset")

        args = MagicMock()
        args.dataset = str(dataset_path)
        args.output = "dummy_output.json"
        args.top_k = 5
        args.min_similarity = 0.40
        mock_args.return_value = args

        def _fake_memory(*args, **kwargs):
            # Inject a fake embedder so the test does not download the real model.
            return IncidentMemory(embedder=FakeEmbedder(), *args, **kwargs)

        with patch("phase2.pipeline.IncidentMemory", side_effect=_fake_memory):
            # Test the main entry point which does a full E2E execution
            main()

        # Assert that write_text was called with a JSON payload
        self.assertTrue(mock_write.called)

        payload_str = mock_write.call_args[0][0]
        payload = json.loads(payload_str)

        # Assert that it matches Phase2BatchOutput
        Phase2BatchOutput.model_validate(payload)
