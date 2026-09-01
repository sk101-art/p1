"""Shared fixtures for laptop1 integration tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the worktree root is importable.
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.integration.fixtures.fixture_loader import (  # noqa: E402
    get_or_create,
)


@pytest.fixture
def synthetic_dataset_path(tmp_path: Path) -> Path:
    """A small, fresh synthetic Phase 1 dataset written to a temp dir."""
    return get_or_create(num_incidents=6, fixture_file=str(tmp_path / "ds.json"))


@pytest.fixture
def state_db_path(tmp_path: Path) -> Path:
    """A fresh SQLite state DB path under a temp dir."""
    return tmp_path / "state" / "test_laptop1.db"


@pytest.fixture
def runtime(tmp_path: Path):
    """A warmed Phase 2 runtime (isolated chroma dir per fixture)."""
    from integration.laptop1.phase2_runtime import Phase2Runtime, Phase2RuntimeConfig

    cfg = Phase2RuntimeConfig(
        chroma_dir=tmp_path / "chroma",
        collection_name="test_integration_collection",
    )
    rt = Phase2Runtime(cfg)
    rt.warm()
    yield rt
    rt.close()
