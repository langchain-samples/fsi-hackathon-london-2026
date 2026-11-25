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
- `adapters/regulatory/` — Driven adapters for FCA Handbook and Consumer Credit Act (MCP clients and mocks).

### 2. What an Outbound Adapter Is (Two Distinct Components)

The outbound layer consists of **two separate adapters** that compose together:

1. **API / Tool Outbound Adapter** (e.g., `adapters/outbound/github_client.py`) — purely wraps and abstracts an external API or CLI (`git`, `gh`, PDF renderer, etc.) behind a clean protocol (`GitHubClient`). Contains **no** LLM reasoning or compliance reaction logic.
2. **Outbound Agent / Behaviour Adapter** (e.g., `adapters/outbound/code_remediator.py`) — defines **how a module reacts to `FindingsReport`** (including any module-specific LLM reasoning, code remediation, planning, or LangGraph subgraph) and **references the API outbound adapter** (e.g., `GitHubClient`) to carry out external side effects like pushing branches, opening PRs, or posting comments.

Keep outbound agent behaviour inside `adapters/outbound/` (referencing the API adapter), never inside `core/` or `adapters/llm/`.

### 3. Production Defaults (No Silent Mocks)

- Default to **real implementations** everywhere they exist:
  - Real LLM via `.env` (`LangChainComplianceAnalyzer` / `LangChainCodeRemediator`).
  - Real Consumer Credit Act 1974 corpus via `fca-regulatory-mcp` (auto-bootstrapped HTTP in `scripts/run.sh`, or stdio MCP in `agent.py` when `mcp` is installed).
  - Real GitHub outbound actions by default: `codebase_review` opens a remediation PR via `git` + `gh pr create`; `pr_review` posts a PR review comment via `gh pr comment`.
- Only use mocks by default where no real implementation exists yet (currently the FCA Handbook corpus inside `fca-regulatory-mcp`, and the `product_spec` PDF stub).
- Keep opt-out flags (`--no-github`, `--no-mcp`, `--synthetic-diff`) and dependency injection for offline, deterministic unit tests.

### 4. Testing Strategy (Real Behaviour Adapters + Mock API Adapters)

- Tests must exercise the **real inbound/outbound behaviour adapters** (`build_github_codebase_review_module`, `build_github_pr_review_module`, `make_code_remediation_outbound`), not behaviour stubs (except for modules whose real adapter does not exist yet, such as `product_spec`).
- Mock only at the **external API / tool boundary** by injecting mock API adapters — e.g., `LocalMockGitHubClient` (from `adapters/outbound/github_client.py`), which simulates repo clones, PR diffs, branch pushes, PR creation, and PR comments by writing the generated artifacts to local disk and reporting a summary to stdout.

