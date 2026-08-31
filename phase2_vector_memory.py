"""Backward-compatible CLI and import surface for the Phase 2 memory engine."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from phase2.memory import IncidentMemory
from phase2.pipeline import process_dataset

DEFAULT_DATASET = Path("frontend_data/unified_master_dataset.json")
_memory: IncidentMemory | None = None


def get_memory() -> IncidentMemory:
    global _memory
    if _memory is None:
        _memory = IncidentMemory()
    return _memory


def index_unified_dataset(dataset_path: str | Path = DEFAULT_DATASET) -> dict[str, Any]:
    path = Path(dataset_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    report, _ = process_dataset(raw, get_memory())
    return report.model_dump(mode="json")


def query_similar_incident(
    new_log_template: str,
    top_k: int = 5,
    target_service: str | None = None,
    min_similarity: float = 0.40,
) -> dict[str, Any]:
    matches = get_memory().search(
        new_log_template,
        top_k=top_k,
        target_service=target_service,
        min_similarity=min_similarity,
    )
    return {"matches": [match.model_dump(mode="json") for match in matches]}


if __name__ == "__main__":
    print(json.dumps(index_unified_dataset(), indent=2))
