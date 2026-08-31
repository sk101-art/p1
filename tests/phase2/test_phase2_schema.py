import json
import tempfile
import unittest
from pathlib import Path

from phase2.schema import write_contract_schemas


class SchemaTests(unittest.TestCase):
    def test_versioned_contract_schemas_are_generated(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = write_contract_schemas(directory)
            self.assertEqual(len(paths), 4)
            for path in paths:
                schema = json.loads(Path(path).read_text(encoding="utf-8"))
                self.assertEqual(schema["type"], "object")
                self.assertIn("properties", schema)
                self.assertFalse(schema.get("additionalProperties", True))
