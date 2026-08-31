"""Generate immutable JSON Schema artifacts for integration consumers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .models import Phase2BatchOutput, Phase2InputEnvelope, Phase2Result
from .phase3_contract import Phase3Input


def write_contract_schemas(output_dir: str | Path) -> list[Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    contracts = {
        "phase2_input_v1.schema.json": Phase2InputEnvelope.model_json_schema(),
        "phase2_result_v1.schema.json": Phase2Result.model_json_schema(),
        "phase2_batch_output_v1.schema.json": Phase2BatchOutput.model_json_schema(),
        "phase3_input_v1.schema.json": Phase3Input.model_json_schema(),
    }
    written = []
    for filename, schema in contracts.items():
        target = directory / filename
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(json.dumps(schema, indent=2), encoding="utf-8")
        temporary.replace(target)
        written.append(target)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Phase 2 JSON Schemas")
    parser.add_argument("--output-dir", default="contracts")
    args = parser.parse_args()
    for path in write_contract_schemas(args.output_dir):
        print(path)


if __name__ == "__main__":
    main()
