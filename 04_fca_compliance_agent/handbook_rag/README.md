# FCA Handbook RAG

Search over the FCA Handbook with [Qdrant](https://qdrant.tech), as an MCP server. The repo holds no FCA text: you download
it (below). `data/sample` is **fictional** (XSYS, XPRN), for tests, and must not be cited. See [PLAN.md](PLAN.md).

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

## Load the real Handbook

Each person downloads with their own free [FCA Handbook account](https://handbook.fca.org.uk/handbook-api). Put the token in
`.env.fca` in the repo root, as `FCA_HANDBOOK_API_TOKEN=...`. It is git-ignored, and only this script reads it:

```bash
uv run --no-sync python -m scripts.download
```

```bash
uv run --no-sync python -m scripts.ingest data/snapshots/<date>
```

That is SYSC, PRIN, COBS, CONC and the Glossary: 130 chapters, about 5 minutes to download and 3 to embed. The text
and the index stay on this machine (both are git-ignored). A partial download (`--sourcebooks`, `--chapters`) goes into a
folder of its own, and a snapshot is never replaced by one of another day or coverage. © FCA, used under the Open Government Licence v3.0.

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

The core agent connects with `FCA_MCP_URL=http://127.0.0.1:8765/mcp` (see the [core README](../README.md)).

| Tool | Job | Returns |
|------|-----|---------|
| `search_fca_handbook` | Breadth: which rules might matter? | A short list: reference, heading, type and a snippet |
| `get_fca_section` | Depth: what does the whole section say? | Every rule and guidance paragraph in order, definitions used, and links outside the section |
| `check_fca_citation` | Verification: is the quote real? | OK, or the problem, and whether the provision is binding |

A section over 12,000 characters is cut down, never mid-provision: to the group under the provision's sub-heading, or to
its neighbours on each side. `complete` is then false and `outline` lists the rest. Failures are MCP errors, never text.

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
`.env.fca` in the repo root, not in the shared `.env`, and never commit them.
