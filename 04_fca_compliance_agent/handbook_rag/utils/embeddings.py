"""Dense vectors (meaning) and sparse vectors (exact words and rule references).

The sparse side is a small BM25 encoder rather than a downloaded model, so a reference such as "SYSC 15A.2.1R" can be
one token instead of being split apart. Qdrant applies the IDF part of BM25 itself.
"""

import hashlib
import math
import os
import re
import threading
from collections import Counter
from functools import lru_cache
from pathlib import Path

from langchain_core.embeddings import Embeddings
from qdrant_client.models import SparseVector

from utils.references import ancestors, find_references

_STOPWORDS = frozenset(
    "a an and any are as at be been by for from has have if in is it its may must not of on or such that the their "
    "there this to was were which will with".split()
)
_K1, _B, _AVERAGE_LENGTH = 1.2, 0.75, 120
_OWN_REFERENCE_BOOST = 3.0

LOCAL_MODEL = "BAAI/bge-small-en-v1.5"  # the only model `local:` accepts, so a typo cannot fetch a different one
_local_model_lock = threading.Lock()


class LocalModelNotReady(ValueError):
    """The local model is not in the cache. A ValueError, so the search tools report it instead of raising."""


def _hash(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "big")


def _words(text: str) -> list[str]:
    words = (w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOPWORDS and len(w) > 1)
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words]


def _tokens(text: str) -> list[str]:
    references = [f"ref:{key}" for ref in find_references(text) for key in ref.candidate_keys()]
    return _words(text) + references


def _vector(weights: dict[str, float]) -> SparseVector:
    merged: dict[int, float] = {}
    for token, weight in weights.items():
        index = _hash(token)
        merged[index] = max(merged.get(index, 0.0), weight)
    indices = sorted(merged)
    return SparseVector(indices=indices, values=[merged[i] for i in indices])


def sparse_document(text: str, own_key: str) -> SparseVector:
    tokens = _tokens(text)
    length_norm = _K1 * (1 - _B + _B * len(tokens) / _AVERAGE_LENGTH)
    weights = {t: n * (_K1 + 1) / (n + length_norm) for t, n in Counter(tokens).items()}
    # A search for a reference should find the provision itself before the ones that merely cite it.
    weights[f"ref:{own_key}"] = _OWN_REFERENCE_BOOST
    for parent in ancestors(own_key):
        weights[f"ref:{parent}"] = max(1.0, weights.get(f"ref:{parent}", 0.0))
    return _vector(weights)


def sparse_query(text: str) -> SparseVector:
    return _vector(dict.fromkeys(_tokens(text), 1.0))


class HashEmbeddings(Embeddings):
    """Offline stand-in for tests: matches shared words only, with no understanding of meaning."""

    def __init__(self, size: int = 512):
        self.size = size

    def embed_query(self, text: str) -> list[float]:
        vector = [0.0] * self.size
        for word in _words(text):
            vector[_hash(word) % self.size] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]


def model_cache_dir() -> Path:
    """FastEmbed's own default is a temporary folder that the system may clear."""
    return Path(os.environ.get("FASTEMBED_CACHE_PATH") or "~/.cache/fastembed").expanduser()


def _load_local_model(folder: Path, *, download: bool):
    from fastembed import TextEmbedding

    try:
        return TextEmbedding(LOCAL_MODEL, cache_dir=str(folder), local_files_only=not download)
    except Exception as error:
        reason = "could not be downloaded to" if download else "is not in"
        raise LocalModelNotReady(
            f"The embedding model {LOCAL_MODEL} {reason} {folder} (or the copy there is damaged). To download it "
            f"(about 70 MB, from Hugging Face), run `uv run --no-sync python -m utils.embeddings` in "
            f"04_fca_compliance_agent/handbook_rag. Cause: {error}"
        ) from error


@lru_cache
def _cached_local_model(folder: Path):
    return _load_local_model(folder, download=False)


class LocalEmbeddings(Embeddings):
    """The local model, run through FastEmbed, so embedding sends no text off this machine.

    The model is loaded on first use, from the cache only: searching never downloads anything.
    """

    def _loaded(self):
        # The lock stops parallel first searches from loading it twice. Embedding needs no lock: FastEmbed keeps no
        # state between calls once the model is loaded.
        with _local_model_lock:
            return _cached_local_model(model_cache_dir())

    def embed_query(self, text: str) -> list[float]:
        # FastEmbed adds no instruction prefix for this model, so queries and documents are embedded alike.
        return next(iter(self._loaded().query_embed(text))).tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._loaded().passage_embed(texts)]


@lru_cache
def get_embeddings(name: str) -> Embeddings:
    """`hash` for offline tests, otherwise `provider:model`. OpenAI uses OPENAI_BASE_URL, so it goes via the gateway."""
    if name == "hash":
        return HashEmbeddings()
    provider, _, model = name.partition(":")
    if provider == "local":
        if model != LOCAL_MODEL:
            raise ValueError(f"The only local embedding model is local:{LOCAL_MODEL}, not {name}")
        return LocalEmbeddings()
    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        # Send plain strings: pre-tokenised input is not accepted by every gateway. Chunks are far below the limit.
        return OpenAIEmbeddings(model=model, check_embedding_ctx_length=False)
    from langchain.embeddings import init_embeddings

    return init_embeddings(name)


if __name__ == "__main__":
    from utils import settings  # noqa: F401  reads FASTEMBED_CACHE_PATH from .env, as searching does

    _load_local_model(model_cache_dir(), download=True)
    print(f"{LOCAL_MODEL} is in {model_cache_dir()}")
