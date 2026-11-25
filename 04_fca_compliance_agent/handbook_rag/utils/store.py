"""The Qdrant index: building it from a snapshot, and searching it.

Each snapshot goes into new collections. An alias is moved to them only once they are complete, so a failed refresh
never damages the index in use, and an older snapshot can be put back by moving the alias again.
"""

import hashlib
import re
import threading
import uuid
from datetime import date
from functools import lru_cache

from qdrant_client import QdrantClient, models as m

from utils.embeddings import get_embeddings, sparse_document, sparse_query
from utils.references import Reference, find_references, parse_reference, section_of
from utils.schema import Provision, Snapshot
from utils.settings import get_settings

_NAMESPACE = uuid.UUID("7f6f6a3e-5d1c-4a52-9a53-0a4f6d0b1c11")
_BATCH = 64
_FORMAT = 2  # raise this when what is stored changes, so the next ingest rebuilds the index
_MAX_CHARS = 1500  # the local model reads 512 tokens and silently ignores the rest
_BINDING = ("R", "UK", "EU", "D")  # rules, retained law and directions; not guidance (G) or evidential (E)
_OPEN_ENDED = date.max.toordinal()
# Local Qdrant is in-process and not built for parallel access; sub-agents search in parallel threads.
_lock = threading.RLock()


def is_binding(provision_type: str) -> bool | None:
    """None if the Handbook gives no type, as for transitional tables and schedules."""
    return provision_type in _BINDING if provision_type else None


class IndexNotReady(RuntimeError):
    """The index is missing, or was built with a different embedding model."""


@lru_cache
def _client(url: str | None, api_key: str | None, path: str) -> QdrantClient:
    if url:
        return QdrantClient(url=url, api_key=api_key)
    return QdrantClient(path=path, force_disable_check_same_thread=True)


def client() -> QdrantClient:
    s = get_settings()
    with _lock:
        return _client(s.qdrant_url, s.qdrant_api_key, str(s.qdrant_path))


def close() -> None:
    """Release the local folder, so another process can open it."""
    with _lock:
        if _client.cache_info().currsize:
            client().close()
        _client.cache_clear()


def split_text(text: str, limit: int = _MAX_CHARS) -> list[str]:
    """Whole provisions stay together. Only very long ones are split, at line breaks, then at spaces."""
    parts, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:cut])
            line = line[cut:].lstrip()
        if current and len(current) + len(line) + 1 > limit:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current.strip():
        parts.append(current)
    return parts


def _search_text(p: Provision, part: str) -> str:
    return f"{p.citation}\n{' > '.join(p.heading_path)}\n{part}"


def build_index(snapshot: Snapshot, alias: str) -> str:
    """Index a snapshot, check it is complete, then point the alias at it. Returns the collection name."""
    s, c = get_settings(), client()
    embeddings = get_embeddings(s.embedding_model)
    chunks = [(p, i, part) for p in snapshot.provisions for i, part in enumerate(split_text(p.text))]
    position = {id(p): n for n, p in enumerate(snapshot.provisions)}  # a snapshot lists provisions in Handbook order
    model_id = hashlib.sha256(f"{_FORMAT}|{s.embedding_model}".encode()).hexdigest()[:6]
    name = f"{alias}__{snapshot.manifest.snapshot_date:%Y%m%d}_{snapshot.sha256[:8]}_{model_id}"
    glossary = f"{name}__glossary"

    size = len(embeddings.embed_query("size check"))
    with _lock:
        for collection in (name, glossary):
            if c.collection_exists(collection):
                if collection == _alias_target(c, alias):
                    return name  # the same snapshot and model are already live
                c.delete_collection(collection)
        c.create_collection(
            name,
            vectors_config={"dense": m.VectorParams(size=size, distance=m.Distance.COSINE)},
            sparse_vectors_config={"sparse": m.SparseVectorParams(modifier=m.Modifier.IDF)},
        )
        c.create_collection(glossary, vectors_config=m.VectorParams(size=1, distance=m.Distance.COSINE))
        if s.qdrant_url:  # local mode has no payload indexes
            for field in ("key", "sourcebook", "chapter", "section", "provision_type"):
                c.create_payload_index(name, field, m.PayloadSchemaType.KEYWORD)
            for field in ("from_day", "to_day"):
                c.create_payload_index(name, field, m.PayloadSchemaType.INTEGER)
            c.create_payload_index(glossary, "term_key", m.PayloadSchemaType.KEYWORD)

    for start in range(0, len(chunks), _BATCH):
        batch = chunks[start : start + _BATCH]
        texts = [_search_text(p, part) for p, _, part in batch]
        points = [
            m.PointStruct(
                id=str(uuid.uuid5(_NAMESPACE, f"{p.citation}|{p.effective_from}|{i}")),
                vector={"dense": dense, "sparse": sparse_document(text, p.key)},
                payload=_payload(p, i, part, position[id(p)], snapshot, s.embedding_model),
            )
            for (p, i, part), text, dense in zip(batch, texts, embeddings.embed_documents(texts), strict=True)
        ]
        with _lock:
            c.upsert(name, points, wait=True)

    terms = [
        m.PointStruct(
            id=str(uuid.uuid5(_NAMESPACE, f"term|{t.term}|{t.definition}")),
            vector=[1.0],
            payload={"term": t.term, "term_key": t.term.casefold().strip(), "definition": t.definition,
                     "source": snapshot.manifest.source},
        )
        for t in snapshot.glossary
    ]
    with _lock:
        for start in range(0, len(terms), _BATCH):
            c.upsert(glossary, terms[start : start + _BATCH], wait=True)
        stored = c.count(name, exact=True).count
        if stored != len(chunks):
            raise RuntimeError(f"expected {len(chunks)} chunks but {stored} were stored; the alias was not moved")
        _move_alias(c, alias, name)
    return name


def _payload(
    p: Provision, part_number: int, part: str, position: int, snapshot: Snapshot, embedding_model: str
) -> dict:
    return {
        # full_text means that reading never depends on how the text was split for searching
        "key": p.key, "citation": p.citation, "sourcebook": p.sourcebook, "provision_type": p.provision_type,
        "heading_path": p.heading_path, "text": part, "part": part_number,
        "full_text": p.text if part_number == 0 else None,
        "cites": p.cites,
        "chapter": p.chapter, "section": p.section, "subheading": p.subheading, "position": position,
        "url": p.url,
        "effective_from": p.effective_from and p.effective_from.isoformat(),
        "effective_to": p.effective_to and p.effective_to.isoformat(),
        "from_day": p.effective_from.toordinal() if p.effective_from else 0,
        "to_day": p.effective_to.toordinal() if p.effective_to else _OPEN_ENDED,
        "snapshot_date": snapshot.manifest.snapshot_date.isoformat(), "source": snapshot.manifest.source,
        "attribution": snapshot.manifest.attribution, "embedding_model": embedding_model,
    }  # fmt: skip


def _alias_target(c: QdrantClient, alias: str) -> str | None:
    return next((a.collection_name for a in c.get_aliases().aliases if a.alias_name == alias), None)


def _move_alias(c: QdrantClient, alias: str, name: str) -> None:
    operations: list = []
    if _alias_target(c, alias):
        operations.append(m.DeleteAliasOperation(delete_alias=m.DeleteAlias(alias_name=alias)))
    operations.append(m.CreateAliasOperation(create_alias=m.CreateAlias(collection_name=name, alias_name=alias)))
    c.update_collection_aliases(operations)


def _live() -> tuple[QdrantClient, str]:
    s, c = get_settings(), client()
    name = _alias_target(c, s.collection)
    if name is None:
        raise IndexNotReady(f"There is no Handbook index called {s.collection!r}. Run scripts/ingest.py first.")
    return c, name


_SCOPE_FIELDS = ("sourcebook", "chapter", "section")


def _filter(
    as_of: date, scope: list[str] | None = None, must: list | None = None, fields: tuple[str, ...] = _SCOPE_FIELDS
) -> m.Filter:
    conditions: list = [
        m.FieldCondition(key="from_day", range=m.Range(lte=as_of.toordinal())),
        m.FieldCondition(key="to_day", range=m.Range(gte=as_of.toordinal())),
    ]
    if scope:
        conditions.append(m.Filter(should=[
            m.FieldCondition(key=field, match=m.MatchAny(any=scope)) for field in fields
        ]))  # fmt: skip
    return m.Filter(must=conditions + (must or []))


def search(query: str, as_of: date, scope: list[str] | None = None, limit: int = 8) -> list[dict]:
    """Hybrid search over the provisions in force on a date. Ranks are fused, so the score is not a confidence."""
    s = get_settings()
    where = _filter(as_of, scope)
    with _lock:
        c, name = _live()
        sample = c.scroll(name, limit=1)[0]
        if sample and sample[0].payload["embedding_model"] != s.embedding_model:
            raise IndexNotReady(
                f"The index was built with {sample[0].payload['embedding_model']} but EMBEDDING_MODEL is "
                f"{s.embedding_model}. Use the same model, or run scripts/ingest.py again."
            )
    dense = get_embeddings(s.embedding_model).embed_query(query)  # may call an API, so not under the lock
    with _lock:
        c, name = _live()
        points = c.query_points(
            name,
            prefetch=[
                m.Prefetch(query=dense, using="dense", filter=where, limit=limit * 5),
                m.Prefetch(query=sparse_query(query), using="sparse", filter=where, limit=limit * 5),
            ],
            query=m.FusionQuery(fusion=m.Fusion.RRF),
            limit=limit * 3,
            with_payload=True,
        ).points
    best: dict[tuple, dict] = {}  # several parts of one long provision count once
    for ref in find_references(query):  # a provision named in the query always comes first
        row = get(str(ref), as_of)
        if row and (not scope or any(row[field] in scope for field in _SCOPE_FIELDS)):
            best[(row["citation"], row["effective_from"])] = row
    for point in points:
        best.setdefault((point.payload["citation"], point.payload["effective_from"]), point.payload)
    return list(best.values())[:limit]


def _scroll_all(c: QdrantClient, name: str, where: m.Filter) -> list[dict]:
    payloads, offset = [], None
    while True:
        points, offset = c.scroll(name, scroll_filter=where, limit=256, offset=offset)
        payloads += [p.payload for p in points]
        if offset is None:
            return payloads


def get(ref: str, as_of: date) -> dict | None:
    """One provision in force on a date, in full."""
    parsed: Reference | None = parse_reference(ref)
    if parsed is None:
        return None
    with _lock:
        c, name = _live()
        for key in parsed.candidate_keys():
            rows = _scroll_all(c, name, _filter(as_of, must=[m.FieldCondition(key="key", match=m.MatchValue(value=key))]))
            # "X 1.1.1R" must not return "X 1.1.1G", unless the letter turned out to be part of the number.
            if key == parsed.key and parsed.type:
                rows = [r for r in rows if r["provision_type"] == parsed.type]
            if rows:
                newest = max((r for r in rows if r["part"] == 0), key=lambda r: r["from_day"])
                return {**newest, "text": newest["full_text"]}
    return None


def get_section(section: str, as_of: date) -> list[dict]:
    """Every provision in force in a section, in full and in Handbook order."""
    with _lock:
        c, name = _live()
        rows = _scroll_all(c, name, _filter(as_of, must=[
            m.FieldCondition(key="section", match=m.MatchValue(value=section))]))  # fmt: skip
    newest: dict[str, dict] = {}
    for row in rows:
        if row["part"] == 0 and row["from_day"] >= newest.get(row["citation"], row)["from_day"]:
            newest[row["citation"]] = {**row, "text": row["full_text"]}
    return sorted(newest.values(), key=lambda r: r["position"])


def sections(chapter: str, as_of: date) -> list[str]:
    """The sections in a chapter or sourcebook that have provisions in force, in Handbook order."""
    with _lock:
        c, name = _live()
        rows = _scroll_all(c, name, _filter(as_of, [chapter], fields=("sourcebook", "chapter")))
    return list(dict.fromkeys(r["section"] for r in sorted(rows, key=lambda r: r["position"])))


def scopes() -> set[str]:
    """Every sourcebook, chapter and section in the index."""
    with _lock:
        c, name = _live()
        rows = _scroll_all(c, name, m.Filter())
    return {r[field] for r in rows for field in _SCOPE_FIELDS}


def definitions(text: str, limit: int = 10) -> dict[str, str]:
    """Glossary terms used in the text. Long terms first, as they are the more specific ones."""
    with _lock:
        c, name = _live()
        rows = _scroll_all(c, f"{name}__glossary", m.Filter())
    lowered = text.casefold()
    used = [r for r in rows if re.search(rf"\b{re.escape(r['term_key'])}s?\b", lowered)]
    used.sort(key=lambda r: -len(r["term"]))
    return {r["term"]: r["definition"] for r in used[:limit]}
