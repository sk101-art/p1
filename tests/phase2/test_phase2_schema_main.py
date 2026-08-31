import unittest
from unittest.mock import patch, MagicMock

from phase2.schema import main

class SchemaMainTests(unittest.TestCase):
    @patch("phase2.schema.argparse.ArgumentParser.parse_args")
    @patch("phase2.schema.Path.write_text")
    @patch("phase2.schema.Path.replace")
    @patch("phase2.schema.Path.mkdir")
    @patch("builtins.print")
    def test_schema_main(self, mock_print, mock_mkdir, mock_replace, mock_write, mock_args):
        args = MagicMock()
        args.output_dir = "dummy_dir"
        mock_args.return_value = args
        
        main()
        
        # Check that 4 schemas were written (phase2_input, phase2_result,
        # phase2_batch_output, phase3_input)
        self.assertEqual(mock_write.call_count, 4)
        self.assertEqual(mock_print.call_count, 4)
