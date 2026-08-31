"""Phase 2 runtime wrapper.

Wraps the FROZEN Phase 2 ``process_phase1_dataset`` entry point and its
``IncidentMemory`` so the integration service can warm the embedding model and
chromadb once at startup, then reuse them across runs.

This module imports from the frozen ``phase2`` package only. It must never
modify Phase 2 internals.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from phase2.config import (
    DEFAULT_CHROMA_DIR,
    DEFAULT_COLLECTION_NAME,
    DEFAULT_EMBED_MODEL,
    DEFAULT_TOP_K,
    PROVISIONAL_MIN_SIMILARITY,
)
from phase2.memory import IncidentMemory
from phase2.models import Phase1Dataset, Phase2BatchOutput
from phase2.pipeline import process_phase1_dataset


@dataclass
class Phase2RuntimeConfig:
    chroma_dir: Path = DEFAULT_CHROMA_DIR
    collection_name: str = DEFAULT_COLLECTION_NAME
    embed_model: str = DEFAULT_EMBED_MODEL
    top_k: int = DEFAULT_TOP_K
    min_similarity: float = PROVISIONAL_MIN_SIMILARITY
    timeout_seconds: float = float(os.getenv("PHASE2_TIMEOUT_SECONDS", "15.0"))


class Phase2Runtime:
    """Owns a warmed IncidentMemory and exposes a single process call."""

    def __init__(self, config: Optional[Phase2RuntimeConfig] = None) -> None:
        self.config = config or Phase2RuntimeConfig()
        self._memory: Optional[IncidentMemory] = None
        self._ready = False

    def warm(self) -> None:
        """Load embedder + chromadb collection (heavy, do once at startup)."""
        if self._ready:
            return
        self._memory = IncidentMemory(
            persist_dir=str(self.config.chroma_dir),
            collection_name=self.config.collection_name,
            embedder=None,  # use default SentenceTransformer
        )
        # Force collection materialization.
        _ = self._memory.collection
        self._ready = True

    @property
    def ready(self) -> bool:
        return self._ready

    def process(self, raw_dataset: dict[str, Any]) -> Phase2BatchOutput:
        if not self._ready or self._memory is None:
            self.warm()
        assert self._memory is not None
        return process_phase1_dataset(
            raw_dataset,
            self._memory,
            top_k=self.config.top_k,
            min_similarity=self.config.min_similarity,
            policy=None,  # use Phase 2 default ActionabilityPolicy
        )

    def close(self) -> None:
        if self._memory is not None:
            try:
                self._memory.close()
            except Exception:
                pass
        self._ready = False
        self._memory = None


__all__ = ["Phase2Runtime", "Phase2RuntimeConfig"]
