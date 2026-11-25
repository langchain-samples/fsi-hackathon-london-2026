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
│   │   ├── regulatory/              # Mock FCA/CCA knowledge bases + MCP adapters (stdio / HTTP)
│   │   ├── llm/                     # LangChain Gateway adapter (reads MODEL_NAME & gateway keys from .env)
│   │   ├── inbound/                 # Stub Inbound Adapter nodes for all 3 modules
│   │   └── outbound/                # Stub Outbound Adapter nodes (incl. agentic subgraph stub for Module 2)
│   └── agent.py                     # Composition root & default compiled graph
└── tests/
    └── test_pipeline.py             # End-to-end tests for map-reduce, all 3 modules, HITL, MCP contract
```

## Quick Start

From the repository root:

```bash
uv sync --all-packages
uv run --no-sync --package fca-compliance-agent python -m unittest discover -s 04_fca_compliance_agent/tests -v
```

### Running the Compliance Agent (macOS & Windows)

Make sure `.env` is configured at the repository root (`cp .env.example .env`), then run:

- **macOS / Linux:**
  ```bash
  ./04_fca_compliance_agent/scripts/run.sh
  # or scan a specific GitHub repo with the live regulatory MCP server:
  ./04_fca_compliance_agent/scripts/run.sh --repo-url https://github.com/natchainsix/quicklend-nc --mcp-url http://127.0.0.1:8001/mcp
  # or choose another module:
  ./04_fca_compliance_agent/scripts/run.sh --module pr_review
  ./04_fca_compliance_agent/scripts/run.sh --module product_spec
  ```
- **Windows (PowerShell):**
  ```powershell
  .\04_fca_compliance_agent\scripts\run.ps1
  .\04_fca_compliance_agent\scripts\run.ps1 --module pr_review
  ```
- **Windows (Command Prompt):**
  ```cmd
  04_fca_compliance_agent\scripts\run.cmd
  04_fca_compliance_agent\scripts\run.cmd --module pr_review
  ```

To enable real MCP server connectivity (for the FCA Handbook RAG and Consumer Credit Act MCP servers):

```bash
uv sync --all-packages --extra mcp
```

## Regulatory MCP Server

`fca-regulatory-mcp` serves the FCA Handbook and Consumer Credit Act lookups over MCP, using the tool names the agent's
MCP adapters expect (`search_fca_handbook`, `get_fca_rule`, `query_consumer_credit_act`,
`get_consumer_credit_act_document`):

```bash
uv sync --all-packages --extra mcp
uv run fca-regulatory-mcp                      # stdio: the agent spawns it as a subprocess
uv run fca-regulatory-mcp --http --port 8001   # Streamable HTTP
```

Connect the agent to the HTTP server with `FCA_MCP_URL=http://127.0.0.1:8001/mcp` and `CCA_MCP_URL=http://127.0.0.1:8001/mcp`.
The HTTP server exposes all four tools at the same URL. Set both variables to that URL.

- **CCA content** is the Act text in the repo-root `cca.md`. Search returns the best passage from each matching section
  (about 1,200 characters, with a `part` field such as `2 of 5`), each with the reference `CCA 1974 s.<number>`.
  `get_consumer_credit_act_document` returns a whole section.
- **Search** uses BM25 with light stemming, so `withdrawal` and `withdraw` match. An empty result means nothing matched,
  not that the rule does not apply.
- **FCA content** is the mock Handbook corpus from `MockFCAHandbookAdapter`, because the Handbook text is not in this
  repo yet. Those passages carry `"corpus": "mock"`.

## FCA Handbook RAG

[`handbook_rag`](handbook_rag/README.md) is an MCP server over the FCA Handbook: `search_fca_handbook`,
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

Each person loads the real text first (SYSC, PRIN, COBS, CONC and the Glossary): see its README. It has no UK GDPR, which
only the mock rules cover, so with `FCA_MCP_URL` set, UK GDPR rules are not searched.

## Swapping Context Modules

```python
from langgraph.checkpoint.memory import InMemorySaver
from fca_compliance_agent import (
    build_compliance_pipeline,
    build_core_analysis_subgraph,
    MockFCAHandbookAdapter,
    MockConsumerCreditActAdapter,
    LangChainComplianceAnalyzer,
    build_codebase_review_module,
)

core = build_core_analysis_subgraph(
    fca_port=MockFCAHandbookAdapter(),
    cca_port=MockConsumerCreditActAdapter(),
    analyzer_port=LangChainComplianceAnalyzer(),
)

module = build_codebase_review_module()
pipeline = build_compliance_pipeline(
    inbound_adapter=module.inbound_node,
    core_subgraph=core,
    outbound_adapter=module.outbound_node,
    checkpointer=InMemorySaver(),
)
```
