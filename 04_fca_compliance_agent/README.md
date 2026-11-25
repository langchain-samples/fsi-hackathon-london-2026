# 04 — FCA Handbook & Consumer Credit Act Compliance Agent

Hexagonal, LangGraph-based compliance analysis agent designed to plug interchangeably into multiple I/O contexts:

1. **PR Code Review** (`gh` CLI PR link → inline PR comments)
2. **Existing Codebase Review** (GitHub repo URL → agentic remediation PR via Git + `gh` CLI)
3. **Product Spec Review** (Incoming PDF → PDF Findings Report)

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for full architecture diagrams, port contracts, MCP integration guide, and module blueprints.

## Package Layout

```text
04_fca_compliance_agent/
├── ARCHITECTURE.md                  # Full architectural specification & diagrams
├── langgraph.json                   # LangGraph Studio / CLI entrypoint
├── pyproject.toml                   # Package metadata & optional [mcp] extra
├── src/fca_compliance_agent/
│   ├── domain/                      # Pure Pydantic v2 models & LangGraph state schemas
│   ├── ports/                       # Protocol interfaces (Regulatory, LLM, I/O Adapters)
│   ├── core/                        # Core map-reduce LangGraph subgraph + pipeline builder + HITL gate
│   ├── adapters/
│   │   ├── regulatory/              # MCP client adapters (stdio / Streamable HTTP) for FCA Handbook RAG & CCA
│   │   ├── llm/                     # LangChain Gateway adapter (reads MODEL_NAME & gateway keys from .env)
│   │   ├── github_pr.py             # Real PR Review inbound/outbound adapter (`gh` CLI)
│   │   ├── github_codebase.py       # Real Codebase Review inbound scanner + remediation wiring
│   │   └── outbound/                # Real GitHub CLI API adapter & LLM code remediation outbound agent
│   ├── mcp_server/                  # Real Consumer Credit Act 1974 MCP server (`cca.md`) & bootstrap helpers
│   └── agent.py                     # Composition root & default compiled pipelines (real integrations only)
├── handbook_rag/                    # Real FCA Handbook RAG MCP server (Qdrant + local embeddings)
└── tests/
    ├── fakes.py                     # Test-only mocks & stubs (never imported by production code)
    └── test_*.py                    # Unit & integration tests for map-reduce, adapters, HITL, MCP contract
```

## Quick Start

From the repository root:

```bash
uv sync --all-packages
uv run --no-sync --package fca-compliance-agent python -m unittest discover -s 04_fca_compliance_agent/tests -v
```

### Running the Compliance Agent (macOS, Linux & Windows)

Make sure `.env` is configured at the repository root (`cp .env.example .env`), then run a single command. The wrapper script automatically:

1. Finds or installs `uv` if it is not yet on your `PATH`.
2. Syncs `fca-compliance-agent` and its `mcp` / `handbook-rag` extras into `.venv` whenever needed.
3. Connects to or starts both real regulatory MCP HTTP servers (`handbook_rag/mcp_server.py` for the FCA Handbook RAG and `fca-regulatory-mcp` for the Consumer Credit Act 1974), wires `FCA_MCP_URL` / `CCA_MCP_URL`, and shuts down any servers it started when the run finishes.

- **macOS / Linux:**
  ```bash
  # Scan a GitHub repo and open a real remediation PR (auto-bootstrapped real MCP servers):
  ./04_fca_compliance_agent/scripts/run.sh
  ./04_fca_compliance_agent/scripts/run.sh --repo-url https://github.com/natchainsix/safelend-co --branch main
  # Review a real PR and post a real PR comment:
  ./04_fca_compliance_agent/scripts/run.sh --module pr_review --pr-url https://github.com/owner/repo/pull/1
  # Optional overrides:
  ./04_fca_compliance_agent/scripts/run.sh --draft-pr                           # open remediation PR as draft
  ./04_fca_compliance_agent/scripts/run.sh --require-approval                   # interactive HITL gate before GitHub write
  ./04_fca_compliance_agent/scripts/run.sh --no-github                          # local reports only (no PR / no comment)
  ./04_fca_compliance_agent/scripts/run.sh --fca-mcp-url http://127.0.0.1:8765/mcp  # external FCA Handbook RAG MCP
  ./04_fca_compliance_agent/scripts/run.sh --cca-mcp-url http://127.0.0.1:8001/mcp  # external CCA 1974 MCP
  ./04_fca_compliance_agent/scripts/run.sh --fca-mcp-port 8770 --cca-mcp-port 8005  # pin background MCP ports
  ```
- **Windows (PowerShell):**
  ```powershell
  .\04_fca_compliance_agent\scripts\run.ps1
  .\04_fca_compliance_agent\scripts\run.ps1 --repo-url https://github.com/natchainsix/safelend-co --branch main
  .\04_fca_compliance_agent\scripts\run.ps1 --module pr_review --pr-url https://github.com/owner/repo/pull/1
  ```
- **Windows (Command Prompt):**
  ```cmd
  04_fca_compliance_agent\scripts\run.cmd
  04_fca_compliance_agent\scripts\run.cmd --repo-url https://github.com/natchainsix/safelend-co --branch main
  04_fca_compliance_agent\scripts\run.cmd --module pr_review --pr-url https://github.com/owner/repo/pull/1
  ```

Set `FORCE_UV_SYNC=1` if you want the wrapper script to re-run `uv sync` even when `.venv` already has `fca-compliance-agent` and `mcp` installed.

To install the MCP extra manually (e.g. if running `fca-regulatory-mcp` directly):

```bash
uv sync --all-packages --extra mcp
```

## Regulatory MCP Servers

### Consumer Credit Act 1974 (`fca-regulatory-mcp`)

`fca-regulatory-mcp` serves the real Consumer Credit Act 1974 text from the repo-root `cca.md` over MCP
(`query_consumer_credit_act`, `get_consumer_credit_act_document`):

```bash
uv sync --all-packages --extra mcp
uv run fca-regulatory-mcp                      # stdio: the agent spawns it as a subprocess
uv run fca-regulatory-mcp --http --port 8001   # Streamable HTTP (set CCA_MCP_URL=http://127.0.0.1:8001/mcp)
```

- **CCA content** is the Act text in the repo-root `cca.md`. Search returns the best passage from each matching section
  (about 1,200 characters, with a `part` field such as `2 of 5`), each with the reference `CCA 1974 s.<number>`.
  `get_consumer_credit_act_document` returns a whole section.
- **Search** uses BM25 with light stemming, so `withdrawal` and `withdraw` match. An empty result means nothing matched,
  not that the rule does not apply.

### FCA Handbook RAG (`handbook_rag`)

[`handbook_rag`](handbook_rag/README.md) is the real MCP server over the FCA Handbook: `search_fca_handbook`,
`get_fca_section` and `check_fca_citation`. Run it in its own terminal, with `fca-regulatory-mcp` for the Consumer Credit Act,
then set both URLs in `.env`:

```bash
cd 04_fca_compliance_agent/handbook_rag && uv run --no-sync python mcp_server.py
```

```bash
uv run --no-sync fca-regulatory-mcp --http --port 8001
```

```
FCA_MCP_URL=http://127.0.0.1:8765/mcp
CCA_MCP_URL=http://127.0.0.1:8001/mcp
```

Each person loads the real text first (SYSC, PRIN, COBS, CONC and the Glossary): see its README.

## Swapping Context Modules

```python
from langgraph.checkpoint.memory import InMemorySaver
from fca_compliance_agent import (
    build_compliance_pipeline,
    build_core_analysis_subgraph,
    build_github_codebase_review_module,
    LangChainComplianceAnalyzer,
    MCPConsumerCreditActAdapter,
    MCPFCAHandbookAdapter,
    MCPServerConfig,
)

core = build_core_analysis_subgraph(
    fca_port=MCPFCAHandbookAdapter(
        server_config=MCPServerConfig(transport="streamable_http", url="http://127.0.0.1:8765/mcp")
    ),
    cca_port=MCPConsumerCreditActAdapter(
        server_config=MCPServerConfig(transport="streamable_http", url="http://127.0.0.1:8001/mcp")
    ),
    analyzer_port=LangChainComplianceAnalyzer(),
)

module = build_github_codebase_review_module()
pipeline = build_compliance_pipeline(
    inbound_adapter=module.inbound_node,
    core_subgraph=core,
    outbound_adapter=module.outbound_node,
    checkpointer=InMemorySaver(),
)
```
