"""BM25 ranking shared by the regulatory MCP server's search tools.

Tokens are lowercased, stopwords are dropped, and a light suffix stripper maps inflections to one stem, so that
'withdrawal', 'withdrawn' and 'withdraw' match each other. It is not a full Porter stemmer; the aim is to make
statute wording and everyday search terms meet, with no dependencies.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable
from typing import Generic, TypeVar

T = TypeVar("T")

_STOPWORDS = frozenset(
    "the a an of to in and or is be for on by with as at that this it its from any are was which who not if "
    "shall must may has have been being such under into their there they them than then also other".split()
)
# Longest first, so 'ations' is removed before 's'. A suffix is only removed when the remaining stem is long enough.
_SUFFIXES = ("ations", "ation", "ments", "ment", "ings", "ing", "ness", "ions", "ion", "ers", "ed", "al", "es", "er", "s")
_MIN_STEM = 4


def _stem(token: str) -> str:
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= _MIN_STEM:
            return token[: -len(suffix)]
    return token


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS]


class BM25(Generic[T]):
    """BM25 over items. `fields` returns (text, weight) pairs; the weight repeats the field's tokens, so a title can
    count more than body text."""

    def __init__(
        self,
        items: Iterable[T],
        fields: Callable[[T], list[tuple[str, int]]],
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self._items: list[T] = list(items)
        self._tf: list[Counter[str]] = []
        for item in self._items:
            tokens: list[str] = []
            for text, weight in fields(item):
                tokens.extend(tokenize(text) * weight)
            self._tf.append(Counter(tokens))
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg = (sum(self._len) / len(self._len)) if self._len else 0.0
        df: Counter[str] = Counter()
        for tf in self._tf:
            df.update(tf.keys())
        n = len(self._items)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self._k1, self._b = k1, b

    def search(
        self,
        query: str,
        limit: int = 5,
        keep: Callable[[T], bool] | None = None,
    ) -> list[tuple[T, float]]:
        """Top items for `query`, best first. Items scoring zero are not returned."""
        terms = set(tokenize(query))
        if not terms:
            return []
        scored: list[tuple[T, float]] = []
        for i, item in enumerate(self._items):
            if keep is not None and not keep(item):
                continue
            tf = self._tf[i]
            norm = self._k1 * (1 - self._b + self._b * self._len[i] / (self._avg or 1.0))
            score = 0.0
            for term in terms:
                freq = tf.get(term)
                if freq:
                    score += self._idf[term] * freq * (self._k1 + 1) / (freq + norm)
            if score > 0:
                scored.append((item, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]
