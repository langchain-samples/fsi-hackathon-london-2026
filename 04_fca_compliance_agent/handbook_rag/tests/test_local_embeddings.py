"""The local embedding model. Tests that need it are skipped when it is not cached, so nothing here downloads it.

To download it once, about 70 MB: `uv run --no-sync python -m utils.embeddings`
"""

import math
from concurrent.futures import ThreadPoolExecutor

import pytest
from tests.conftest import SAMPLE
from tools.handbook import get_fca_section, search_fca_handbook
from utils import embeddings, store
from utils.schema import Snapshot
from utils.settings import get_settings

TODAY = "2026-11-25"
LOCAL = "local:BAAI/bge-small-en-v1.5"


def cites(found):
    return [r["reference"] for r in found["results"]]


@pytest.fixture
def local_model():
    model = embeddings.get_embeddings(LOCAL)
    try:
        model.embed_query("is the model cached?")
    except embeddings.LocalModelNotReady as error:
        pytest.skip(str(error))
    return model


@pytest.fixture
def local_index(local_model, empty_index, monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", LOCAL)
    return store.build_index(Snapshot.load(SAMPLE), "handbook_sample")


def test_it_is_the_default(monkeypatch):
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    assert get_settings().embedding_model == LOCAL


def test_cache_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("FASTEMBED_CACHE_PATH", raising=False)
    assert embeddings.model_cache_dir() == embeddings.Path.home() / ".cache" / "fastembed"
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    assert embeddings.model_cache_dir() == tmp_path


def test_other_local_models_are_refused():
    with pytest.raises(ValueError, match="only local embedding model"):
        embeddings.get_embeddings("local:BAAI/bge-large-en-v1.5")


def test_a_missing_model_is_reported_not_downloaded(index, monkeypatch, tmp_path):
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "empty"))
    # Pretend the index was built with the local model, so the search gets as far as loading it.
    sample = store.client().scroll(index, limit=1)[0][0]
    store.client().set_payload(index, {"embedding_model": LOCAL}, points=[sample.id])
    monkeypatch.setenv("EMBEDDING_MODEL", LOCAL)
    with pytest.raises(embeddings.LocalModelNotReady, match="python -m utils.embeddings"):
        search_fca_handbook("audit", as_of=TODAY)
    assert not any((tmp_path / "empty").rglob("*.onnx"))


def test_vectors(local_model):
    documents = local_model.embed_documents(["Keep audit logs.", "Encrypt customer data."])
    query = local_model.embed_query("Keep audit logs.")
    assert [len(v) for v in (*documents, query)] == [384, 384, 384]
    assert math.isclose(sum(v * v for v in query), 1, abs_tol=1e-4)
    # FastEmbed gives this model no query prefix. If a new version adds one, the index must be rebuilt.
    assert query == pytest.approx(documents[0], abs=1e-4)


def test_parallel_first_searches_load_the_model_once(local_index, monkeypatch):
    loads, load = [], embeddings._load_local_model
    monkeypatch.setattr(embeddings, "_load_local_model", lambda *a, **k: loads.append(1) or load(*a, **k))
    embeddings._cached_local_model.cache_clear()
    embeddings.get_embeddings.cache_clear()
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: search_fca_handbook("audit logs", as_of=TODAY), range(40)))
    assert len(loads) == 1
    assert "XSYS 5.1.1R" in cites(results[0]) and all(cites(r) == cites(results[0]) for r in results)


def test_an_index_built_with_another_model_is_refused(local_model, index, monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", LOCAL)
    with pytest.raises(store.IndexNotReady, match="built with hash"):
        search_fca_handbook("audit", as_of=TODAY)


def test_a_local_index_is_refused_by_another_model(local_index, monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "hash")
    with pytest.raises(store.IndexNotReady, match=f"built with {LOCAL}"):
        search_fca_handbook("audit", as_of=TODAY)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("API secret hardcoded in config file", "XSYS 6.1.1R"),
        ("developer merged their own work without a second pair of eyes", "XSYS 4.2.1R"),
        pytest.param("shipped to production with no QA", "XSYS 4.1.1R", marks=pytest.mark.xfail(
            reason="ranked 6th: the model ranks the guidance on testing, XSYS 4.1.2G, first instead of the rule")),
        ("dark patterns in unsubscribe flow", "XPRN 1.1.2G"),
        ("new npm dependency from an external vendor", "XSYS 7.1.1R"),
        ("TLS turned off", "XSYS 6.1.2R"),
    ],
)  # fmt: skip
def test_search_finds_the_rule_by_meaning(local_index, query, expected):
    assert expected in cites(search_fca_handbook(query, top_k=3, as_of=TODAY))


def test_reading_the_section_recovers_the_rule_that_search_misses(local_index):
    """Search returns the guidance, not the rule. Reading its whole section finds the rule next to it."""
    first = search_fca_handbook("shipped to production with no QA", top_k=3, as_of=TODAY)["results"][0]
    assert "XSYS 4.1.1R" in cites(get_fca_section(first["reference"], as_of=TODAY))
