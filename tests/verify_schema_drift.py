"""Schema drift verification: regenerate JSON schemas from live models and diff against committed contracts."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from phase2.models import (Phase1Incident, Phase2BatchOutput,
                           Phase2RetrievedIncident)
from phase2.schema import write_contract_schemas

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "contracts"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "phase2_schema_contract.json"

CONTRACT_FILES = [
    "phase2_input_v1.schema.json",
    "phase2_result_v1.schema.json",
    "phase2_batch_output_v1.schema.json",
    "phase3_input_v1.schema.json",
]


def verify_schema_drift() -> bool:
    """Regenerate schemas from live Pydantic models and diff against committed contracts.

    Fails if any committed contract differs from the freshly generated schema.
    This catches field additions, removals, type changes, default changes, and
    additionalProperties relaxation that field-name comparison would miss.
    """
    drift_detected = False

    with tempfile.TemporaryDirectory() as tmp:
        generated_dir = Path(tmp)
        write_contract_schemas(generated_dir)

        for filename in CONTRACT_FILES:
            committed = CONTRACTS_DIR / filename
            generated = generated_dir / filename

            if not committed.exists():
                print(f"SCHEMA DRIFT: Missing committed contract {committed}")
                drift_detected = True
                continue

            committed_json = json.loads(committed.read_text(encoding="utf-8"))
            generated_json = json.loads(generated.read_text(encoding="utf-8"))

            if committed_json != generated_json:
                print(f"SCHEMA DRIFT DETECTED in {filename}:")
                print("  Committed contract does not match regenerated schema.")
                print("  Run: python -m phase2.schema --output-dir contracts")
                drift_detected = True

    # Additionally verify Phase 1 / Phase 2 model field contracts against fixture
    if FIXTURE_PATH.exists():
        contract = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        models = {
            "Phase1Incident": Phase1Incident,
            "Phase2RetrievedIncident": Phase2RetrievedIncident,
            "Phase2BatchOutput": Phase2BatchOutput,
        }
        for model_name, model_cls in models.items():
            runtime_fields = set(model_cls.model_fields.keys())
            expected_fields = set(contract.get(model_name, {}).get("properties", []))
            missing = expected_fields - runtime_fields
            added = runtime_fields - expected_fields
            if missing or added:
                print(f"SCHEMA DRIFT DETECTED in {model_name}:")
                if missing:
                    print(f"  Missing fields: {sorted(missing)}")
                if added:
                    print(f"  Unannounced fields: {sorted(added)}")
                drift_detected = True
    else:
        print(f"Schema contract error: Missing fixture {FIXTURE_PATH}")
        drift_detected = True

    if not drift_detected:
        print("Schema verification SUCCESS: Zero contract drift detected.")
        return True
    return False


class TestSchemaDrift(unittest.TestCase):
    def test_schema_drift(self):
        self.assertTrue(verify_schema_drift())


if __name__ == "__main__":
    success = verify_schema_drift()
    sys.exit(0 if success else 1)
