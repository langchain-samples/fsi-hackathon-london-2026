# FCA Handbook RAG

Search over the FCA Handbook with [Qdrant](https://qdrant.tech), for agents that assess pull requests. It holds no real
FCA text yet: see [PLAN.md](PLAN.md). `data/sample` is **fictional** (XSYS, XPRN) and must not be cited.

Set up the shared environment first, from the [top level README](../../README.md#setup). Then add the RAG's packages, which
are an optional extra, by running this from the repo root:

```bash
uv sync --all-packages --extra handbook-rag --extra dev
```

Run everything below from this
folder.

## Embedding model

Embeddings are made on this machine with `bge-small-en-v1.5`. Download it once per PC (about 67 MB, from Hugging Face).
After that it works offline, and nothing else downloads it:

```bash
uv run --no-sync python -m utils.embeddings
```

## Try it offline

`EMBEDDING_MODEL=hash` matches words only. It needs no keys and is for testing.

```bash
EMBEDDING_MODEL=hash uv run --no-sync python -m scripts.ingest data/sample --alias fca_handbook_sample
```

```bash
EMBEDDING_MODEL=hash HANDBOOK_COLLECTION=fca_handbook_sample uv run --no-sync python -m scripts.search "password in source code"
```

```bash
uv run --no-sync pytest -q
```

## Use it in an agent, over MCP

Start the server in its own terminal, and leave it running. It listens on this machine only, at
`http://127.0.0.1:8765/mcp`:

```bash
uv run --no-sync python mcp_server.py
```

The core agent gets its tools from `mcp_client.py`, a single file its project can copy. Set `HANDBOOK_MCP_URL` if the
server is elsewhere.

```python
from mcp_client import call_handbook, load_handbook_tools

agent = create_deep_agent(model=..., tools=load_handbook_tools(), system_prompt=...)
call_handbook("check_citation", {"reference": "SYSC 15A.2.1R", "quote": "..."})  # from plain code, no model
```

| Tool | What it does |
|------|--------------|
| `search_handbook` | Hybrid search, with filters for sourcebook, provision type and date |
| `get_provision` | The full text of one provision in force on a date |
| `lookup_glossary` | What a defined term means |
| `expand_references` | What a provision cites, and what cites it |
| `check_citation` | Whether a provision is in force and a quote from it is word for word. No model is involved |
| `index_status` | Which snapshot is being searched, its date and its sourcebooks |

## Settings

| Key | Default | Used for |
|-----|---------|----------|
| `EMBEDDING_MODEL` | `local:BAAI/bge-small-en-v1.5` | Must be the same when indexing and searching |
| `FASTEMBED_CACHE_PATH` | `~/.cache/fastembed` | Where the model is kept |
| `HANDBOOK_COLLECTION` | `fca_handbook` | Which index to search |
| `QDRANT_PATH` | `.qdrant` | Local index folder. Only one process can open it at a time |
| `QDRANT_URL`, `QDRANT_API_KEY` | none | Set the URL to use a Qdrant server instead |

With the local model, embedding sends nothing off this machine. With OpenAI embeddings, indexed text and queries go to
the gateway. With `LANGSMITH_TRACING=true`, tool inputs and
results also go to LangSmith. FCA credentials are only for downloading data, never for searching: keep them in
`.env.fca` here, not in the shared `.env`, and never commit them.
