from pathlib import Path

import pytest
from utils import store
from utils.schema import Snapshot

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"


@pytest.fixture
def empty_index(tmp_path, monkeypatch):
    """Settings for an empty local index. No network: `hash` embeddings, and tracing off."""
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("EMBEDDING_MODEL", "hash")
    monkeypatch.setenv("HANDBOOK_COLLECTION", "handbook_sample")
    monkeypatch.setenv("QDRANT_URL", "")
    monkeypatch.setenv("QDRANT_PATH", str(tmp_path / "qdrant"))
    store.close()
    yield
    store.close()


@pytest.fixture
def index(empty_index):
    return store.build_index(Snapshot.load(SAMPLE), "handbook_sample")
