# Repository Agent Guidelines (`AGENTS.md`)

## Architectural Rules for `04_fca_compliance_agent`

`04_fca_compliance_agent` follows a strict **Hexagonal Architecture (Ports & Adapters)** built on composable LangGraph subgraphs.

### 1. Inward-Only Dependency Rule

```text
adapters/  ──▶  core/  ──▶  ports/  ──▶  domain/
```

- `domain/` — Pure Pydantic models and LangGraph state schemas only. Never import LLM, MCP, Git, GitHub CLI, or PDF libraries.
- `ports/` — `Protocol` interfaces only (`FCAHandbookPort`, `ConsumerCreditActPort`, `ComplianceAnalyzerPort`, `ContextModule`).
- `core/` — Context-agnostic compliance analysis subgraph (`triage_units -> analyze_unit -> synthesize_report`) and optional HITL gate (`human_review_gate`). Never import concrete adapters or infra libraries.
- `adapters/llm/` — **Driven LLM adapter for `ComplianceAnalyzerPort` only** (used by the Core Analysis Subgraph). Do **not** place module-specific outbound agent logic (such as code remediation or PR drafting) here.
- `adapters/regulatory/` — Driven MCP client adapters for the real FCA Handbook RAG (`handbook_rag/mcp_server.py`) and Consumer Credit Act 1974 (`fca-regulatory-mcp`) servers. Never place mocks here.

### 2. What an Outbound Adapter Is (Two Distinct Components)

The outbound layer consists of **two separate adapters** that compose together:

1. **API / Tool Outbound Adapter** (e.g., `adapters/outbound/github_client.py`) — purely wraps and abstracts an external API or CLI (`git`, `gh`, PDF renderer, etc.) behind a clean protocol (`GitHubClient`). Contains **no** LLM reasoning, compliance reaction logic, or test mocks.
2. **Outbound Agent / Behaviour Adapter** (e.g., `adapters/outbound/code_remediator.py`) — defines **how a module reacts to `FindingsReport`** (including any module-specific LLM reasoning, code remediation, planning, or LangGraph subgraph) and **references the API outbound adapter** (e.g., `GitHubClient`) to carry out external side effects like pushing branches, opening PRs, or posting comments.

Keep outbound agent behaviour inside `adapters/outbound/` (referencing the API adapter), never inside `core/` or `adapters/llm/`.

### 3. Production Code Uses Real Integrations Only (No Stubs or Mocks in Main Code)

- **Main code (`src/` and `scripts/`) must NEVER contain stubs, mocks, or synthetic fallbacks.** Always wire real integrations:
  - Real LLM via `.env` (`LangChainComplianceAnalyzer` / `LangChainCodeRemediator`).
  - Real FCA Handbook RAG via `MCPFCAHandbookAdapter` connected to `handbook_rag/mcp_server.py` (`FCA_MCP_URL`, defaulting to `http://127.0.0.1:8765/mcp` and auto-bootstrapped by `scripts/run.sh`).
  - Real Consumer Credit Act 1974 corpus via `MCPConsumerCreditActAdapter` connected to `fca-regulatory-mcp` (`CCA_MCP_URL` over Streamable HTTP, or stdio in `agent.py`).
  - Real GitHub I/O via `GitHubCLIAdapter` (`git` + `gh`): `codebase_review` opens a remediation PR via `git` + `gh pr create`; `pr_review` reads real PRs via `gh pr view`/`gh pr diff` and posts a PR review comment via `gh pr comment`.
- All stubs and mocks (`LocalMockGitHubClient`, `MockFCAHandbookAdapter`, `MockConsumerCreditActAdapter`, and any test-only module stubs) live strictly under `04_fca_compliance_agent/tests/fakes.py`.

### 4. Testing Strategy (Real Behaviour Adapters + Test-Only Mock API Adapters)

- Tests must exercise the **real inbound/outbound behaviour adapters** (`build_github_codebase_review_module`, `build_github_pr_review_module`, `make_code_remediation_outbound`), never behaviour stubs.
- Mock only at the **external API / tool boundary** by injecting test-only mock adapters from `tests/fakes.py` — e.g., `LocalMockGitHubClient`, which simulates repo clones, PR diffs, branch pushes, PR creation, and PR comments by writing generated artifacts to local disk and reporting a summary to stdout.

