# FCA Handbook & Consumer Credit Act Compliance Agent — Architecture Specification

## 1. Architectural Vision & Principles

This package defines an **encapsulated, reusable compliance analysis agent** built on **Hexagonal Architecture (Ports & Adapters)** and **composable LangGraph subgraphs**.

The core design solves a key asymmetry across compliance use cases:
- **PR Code Review** and **Product Spec Review** produce structured commentary / reports.
- **Existing Codebase Review** must actively edit files, run git operations, and open a remediation Pull Request.

Rather than bloating the compliance reasoning engine with GitHub CLI, Git, or PDF rendering concerns, we separate the lifecycle into **three composable LangGraph stages**:

1. **Inbound Adapter (Node or Subgraph)** — *Artefact Ingestion & Normalisation*  
   Transforms a context-specific input (PR URL, repo URL, PDF path) into a normalised `AnalysisInput` containing self-contained `AnalysisUnit` chunks, plus an opaque pass-through `adapter_context` envelope.
2. **Core Compliance Analysis Agent (Subgraph — Hexagon Interior)** — *Pure Regulatory Analysis*  
   Context-agnostic LangGraph map-reduce subgraph. Triages units for regulatory relevance, queries the **FCA Handbook RAG** and **Consumer Credit Act (CCA)** driven ports in parallel, cross-checks rules against statutory obligations, and synthesises a single typed `FindingsReport`.
3. **Outbound Adapter (Node or Subgraph)** — *Deliverable Producer*  
   Because a compiled LangGraph `StateGraph` is itself a valid LangGraph node, an Outbound Adapter can be either **deterministic** (PDF report generator, `gh pr review` commenter) **or fully agentic** with its own private tools and ReAct loop (e.g., a remediation coding sub-agent that edits files, runs tests, commits, and opens a PR via `gh`).

### Strict Dependency Rule (Inward-Only)

```
adapters/  ──▶  core/  ──▶  ports/  ──▶  domain/
```

- `domain/` depends **only** on Python stdlib + `pydantic` (+ `typing_extensions` / `langgraph` reducer annotations in `state.py`). Zero LLM, MCP, GitHub, or PDF imports.
- `ports/` depends **only** on `domain/`. Defines `Protocol` interfaces for external regulatory tools, LLM reasoning, and I/O adapter nodes.
- `core/` depends **only** on `domain/` and `ports/` (+ `langgraph`). Never imports concrete MCP clients, LangChain model providers, `gh` CLI, Git, or PDF libraries.
- `adapters/` implements `ports/` (MCP servers, mocks, LangChain LLM Gateway, and the swappable I/O modules).
- `agent.py` (Composition Root) wires concrete adapters into the core pipeline.

---

## 2. System Topology

### 2.1 Hexagonal Architecture Overview

```mermaid
flowchart LR
    subgraph Driving["Driving / Inbound Adapters (LangGraph Nodes or Subgraphs)"]
        IN_PR["PR Review Inbound\n(gh pr diff -> AnalysisUnits)"]
        IN_REPO["Codebase Inbound\n(git clone -> AnalysisUnits)"]
        IN_PDF["Product Spec Inbound\n(PDF parse -> AnalysisUnits)"]
    end

    subgraph Hexagon["Core Compliance Agent (Hexagon Interior - LangGraph Subgraph)"]
        TRIAGE["triage_units\n(Relevance Filter)"]
        ANALYZE["analyze_unit x N\n(Parallel Send Fan-Out)"]
        SYNTH["synthesize_report\n(Deduplicate & Verdict)"]
        TRIAGE --> ANALYZE --> SYNTH
    end

    subgraph Gate["Optional Governance"]
        HITL["human_review_gate\n(LangGraph interrupt)"]
    end

    subgraph RegPorts["Driven Regulatory Ports (Protocols)"]
        P_FCA["FCAHandbookPort"]
        P_CCA["ConsumerCreditActPort"]
    end

    subgraph MCPAdapters["Driven MCP Adapters (Other Team's Servers)"]
        M_FCA["MCPFCAHandbookAdapter\n(stdio / Streamable HTTP)"]
        M_CCA["MCPConsumerCreditActAdapter\n(stdio / Streamable HTTP)"]
    end

    subgraph Outbound["Driven / Outbound Adapters (LangGraph Nodes or Agentic Subgraphs)"]
        OUT_PR["PR Commenter Node\n(gh pr review)"]
        OUT_REPO["Remediation Coding Subgraph\n(file edit + git + gh pr create)"]
        OUT_PDF["PDF Report Generator Node\n(FindingsReport -> PDF)"]
    end

    IN_PR --> TRIAGE
    IN_REPO --> TRIAGE
    IN_PDF --> TRIAGE

    ANALYZE -.-> P_FCA --> M_FCA
    ANALYZE -.-> P_CCA --> M_CCA

    SYNTH --> HITL
    HITL --> OUT_PR
    HITL --> OUT_REPO
    HITL --> OUT_PDF
```

### 2.2 Pipeline Execution & State Flow

```mermaid
sequenceDiagram
    participant Caller
    participant Inbound as Inbound Adapter Node
    participant Core as Core Analysis Subgraph
    participant MCP as FCA & CCA MCP Adapters
    participant HITL as human_review_gate
    participant Outbound as Outbound Adapter Node/Subgraph

    Caller->>Inbound: raw_input + PipelineConfig
    Inbound-->>Core: AnalysisInput (AnalysisUnits[]) + opaque adapter_context
    Core->>Core: triage_units (filter credit/FCA-relevant units)
    par Parallel Send("analyze_unit") per relevant unit
        Core->>MCP: search_rules(query) [FCA Handbook RAG]
        Core->>MCP: query_provisions / get_document [CCA 1974]
        MCP-->>Core: RegulatoryPassage[]
        Core->>Core: LLM cross-check & grounded Finding[]
    end
    Core->>Core: synthesize_report (dedupe, rank severity, verdict)
    Core-->>HITL: FindingsReport
    alt require_human_approval == true
        HITL-->>Caller: LangGraph interrupt(review_payload)
        Caller->>HITL: Command(resume={action, approved_finding_ids, notes})
    end
    alt approved
        HITL-->>Outbound: FindingsReport + adapter_context
        Outbound-->>Caller: DeliverableResult (PR URL / comments / PDF path)
    else rejected
        HITL-->>Caller: DeliverableResult(status="aborted_by_reviewer")
    end
```

### 2.3 Agentic Architecture: Agents, LLM Steps, Functions & Tools

Which parts reason, which parts are plain code, and which parts are tools. **Today there are no autonomous agents**: LangGraph code fixes the order of steps, the model is called at three points inside them, and code (not the model) calls the MCP tools. The only planned agent is the Module 2 remediation subgraph.

```mermaid
flowchart TB
    START(["Caller"]) --> IN["inbound_adapter<br/>FUNCTION (stub)<br/>PR / repo / PDF to AnalysisUnits"]

    subgraph CORE["core_analysis"]
        TRI("triage_units<br/>LLM STEP 1<br/>relevance + search queries")
        ROUTE["route_triaged_units<br/>FUNCTION<br/>one parallel branch per relevant unit"]

        subgraph ANALYZE["analyze_unit x N"]
            RET["Retrieve and dedupe passages<br/>FUNCTION<br/>runs every query from triage"]
            CHECK("Cross-check unit against rules<br/>LLM STEP 2<br/>drafts Findings")
        end

        subgraph SYNTH["synthesize_report"]
            RANK["Dedupe, rank, assign IDs, verdict<br/>FUNCTION"]
            SUM("Executive summary<br/>LLM STEP 3<br/>cannot change the verdict")
            REPORT["Assemble FindingsReport<br/>FUNCTION"]
        end
    end

    subgraph TOOLS["MCP tools"]
        FCA[/"FCA Handbook search<br/>TOOL<br/>search_fca_handbook"/]
        CCA[/"CCA search<br/>TOOL<br/>query_consumer_credit_act"/]
        CCADOC[/"CCA document fetch<br/>TOOL<br/>get_consumer_credit_act_document"/]
    end

    GATE{"human_review_gate<br/>FUNCTION + optional HUMAN<br/>approve / filter / reject"}

    subgraph OUT["outbound_adapter"]
        OUTPR["Module 1: PR comments<br/>FUNCTION (stub)"]
        OUTREPO["Module 2: remediation<br/>FUNCTION (stub today)<br/>AGENT (planned)"]
        OUTPDF["Module 3: PDF report<br/>FUNCTION (stub)"]
    end

    AGTOOLS[/"TOOLS (planned)<br/>read_file, edit_file, run_tests,<br/>git_checkout_commit, gh pr create"/]
    DONE(["DeliverableResult"])

    subgraph LEGEND["Legend"]
        direction LR
        L1["FUNCTION<br/>plain code"] ~~~ L2("LLM STEP<br/>one model call") ~~~ L3[/"TOOL<br/>called over MCP"/] ~~~ L4{"HUMAN<br/>decision"} ~~~ L5["AGENT<br/>planned"] ~~~ L6[/"TOOL<br/>planned"/]
    end
    LEGEND ~~~ START

    IN --> TRI --> ROUTE
    ROUTE -->|"relevant units"| RET
    ROUTE -->|"no relevant units"| RANK
    RET --> CHECK --> RANK --> SUM --> REPORT --> GATE
    RET <-->|"code calls tools"| TOOLS
    GATE -->|"approved or filtered"| OUTPR & OUTREPO & OUTPDF
    GATE -->|"rejected"| DONE
    OUTREPO <-.->|"agent chooses (planned)"| AGTOOLS
    OUTPR & OUTREPO & OUTPDF --> DONE

    classDef fn fill:#e5e7eb,stroke:#4b5563,color:#111827
    classDef llm fill:#dbeafe,stroke:#1d4ed8,color:#111827
    classDef tool fill:#dcfce7,stroke:#15803d,color:#111827
    classDef human fill:#fef3c7,stroke:#b45309,color:#111827
    classDef agent fill:#f3e8ff,stroke:#7e22ce,color:#111827,stroke-dasharray:6 4
    classDef planned fill:#dcfce7,stroke:#15803d,color:#111827,stroke-dasharray:6 4
    class IN,ROUTE,RET,RANK,REPORT,OUTPR,OUTPDF,L1 fn
    class TRI,CHECK,SUM,L2 llm
    class FCA,CCA,CCADOC,L3 tool
    class GATE,L4 human
    class OUTREPO,L5 agent
    class AGTOOLS,L6 planned
```

**Legend:** grey = deterministic function, blue (rounded) = LLM step, green (slanted) = tool, amber = human decision, purple = planned agent. Dashed outline = not built yet, at least in part. Each pipeline uses one outbound module. The MCP tools are mock adapters unless `FCA_MCP_URL` and `CCA_MCP_URL` are set.

| Kind | What it means here | Parts |
|---|---|---|
| **Agent** | A model chooses its own tools and next step, in a loop | None today. Planned: Module 2 remediation |
| **LLM step** | One model call with a fixed prompt and no tools. Steps 1 and 2 must return a fixed schema; step 3 returns text | `triage_units`, the cross-check in `analyze_unit`, the summary in `synthesize_report` |
| **Function** | Plain code, no model | Inbound adapters, routing, retrieval, deduplication, ranking, verdict, report assembly, outbound adapters |
| **Tool** | An external capability, called over MCP. Code calls it using queries written by LLM step 1 | FCA Handbook search, CCA search, CCA document fetch |
| **Human** | A person decides. Off by default (`require_human_approval=False`), so reports are auto-approved | `human_review_gate` |

- The model's influence on retrieval is limited to the queries and CCA sections it writes in LLM step 1. It cannot search again after seeing the results.
- The verdict is computed by code (`_compute_verdict`), but from severities and statuses the model assigns in LLM step 2.
- Citations and quotes written by the model are not yet checked against the retrieved passages.
- The FCA adapter also supports fetching one rule (`get_fca_rule`), but no core node calls it yet.

---

## 3. Domain Model & State Contracts

### 3.1 Pass-Through `adapter_context` Envelope

A subtle hexagonal problem: the **Codebase Inbound Adapter** clones a repository to `/tmp/work-abc123` (or the **PR Inbound Adapter** resolves `owner/repo`, `pr_number`, and `head_sha`). The Core Agent has no business knowing about filesystem paths or commit SHAs, **yet** the Outbound Adapter needs them to apply edits or post inline PR comments.

**Solution:** `PipelineState` carries a top-level `adapter_context: dict[str, Any]` that flows directly from `inbound_adapter` → `outbound_adapter` and is **never passed into or read by the Core Analysis Subgraph**.

### 3.2 Normalised Input Contract (`AnalysisInput` & `AnalysisUnit`)

Every Inbound Adapter produces an `AnalysisInput`:

| Field | Type | Purpose |
|---|---|---|
| `target_id` | `str` | Unique identifier for the analysed artefact (e.g. `org/repo#42`, `spec-v2.pdf`) |
| `source_type` | `SourceType` | `"pr_review" \| "codebase" \| "product_spec" \| "custom"` |
| `title` | `str` | Human-readable title of the PR, repo, or specification |
| `summary` | `str` | High-level manifest / README / PR description / executive overview |
| `regulatory_scope` | `list[str]` | Optional focus hints (e.g. `["CONC", "PRIN 2A", "CCA 1974"]`) |
| `units` | `list[AnalysisUnit]` | Discrete chunks (diff hunks, source files/modules, PDF sections) |

Each `AnalysisUnit` carries a **discriminated union `location: EvidenceLocation`** so downstream adapters get exact native anchors without the Core knowing adapter internals:
- `PullRequestDiffLocation` — `file_path`, `hunk_header`, `start_line`, `end_line`, `diff_position`
- `CodeLocation` — `file_path`, `start_line`, `end_line`, `symbol_name`
- `DocumentLocation` — `document_name`, `page_start`, `page_end`, `section_heading`
- `GenericLocation` — `reference`

### 3.3 Core Output Contract (`FindingsReport` & `Finding`)

The Core Analysis Subgraph outputs a single `FindingsReport`:
- **`report_id`**, **`target_id`**, **`source_type`**, **`generated_at`**
- **`overall_verdict`**: `"compliant" | "compliant_with_observations" | "non_compliant" | "requires_manual_review"`
- **`executive_summary`**: Concise synthesis of key risks and FCA/CCA alignment
- **`findings: list[Finding]`** — each `Finding` includes:
  - `finding_id`: Stable ID (used by HITL review filtering and deduplication)
  - `unit_id`: Originating `AnalysisUnit.unit_id`
  - `title`, `description`, `severity` (`critical | high | medium | low | info`), `status` (`non_compliant | potential_breach | needs_review | observation`)
  - `location`: Anchored `EvidenceLocation` copied/refined from the analysed unit
  - `evidence_excerpt`: Verbatim snippet from the analysed artefact
  - `fca_citations: list[RegulatoryCitation]` (e.g., `CONC 4.2.5R`, `PRIN 2A.3`)
  - `cca_citations: list[RegulatoryCitation]` (e.g., `CCA 1974 s.55`, `CCA 1974 s.75`)
  - `fca_vs_cca_comparison`: Explicit analysis of how FCA Handbook rules and Consumer Credit Act provisions align, overlap, or create dual obligations for this finding
  - `remediation_guidance`: Concrete, actionable guidance (consumed directly by PDF/PR adapters and as the specification for Module 2's coding sub-agent)
  - `suggested_change`: Optional concise code/text snippet suggestion
  - `confidence`: `0.0`–`1.0`
- **`units_total`**, **`units_triaged_in`**, **`units_skipped`**, **`regulatory_queries_executed`** (full auditability)

---

## 4. Port Contracts

### 4.1 Driven Regulatory Ports (`ports/regulatory.py`)

Both external regulatory sources are abstracted behind async Python `Protocol`s so the Core never depends on MCP transport details or tool schemas:

```python
class FCAHandbookPort(Protocol):
    async def search_rules(
        self,
        query: str,
        *,
        sourcebooks: list[str] | None = None,
        top_k: int = 5,
    ) -> list[RegulatoryPassage]: ...

    async def get_rule(self, reference: str) -> RegulatoryPassage | None: ...


class ConsumerCreditActPort(Protocol):
    async def query_provisions(
        self,
        query: str,
        *,
        sections: list[str] | None = None,
        top_k: int = 5,
    ) -> list[RegulatoryPassage]: ...

    async def get_document(
        self,
        section: str | None = None,
    ) -> list[RegulatoryPassage]: ...
```

Notice why `ConsumerCreditActPort` exposes **both** `query_provisions(...)` and `get_document(section=None)`:
- If the other team ships a RAG/search MCP tool for CCA, `MCPConsumerCreditActAdapter` wires `query_provisions` to it.
- If they ship a full-Act or section-fetch MCP tool, `MCPConsumerCreditActAdapter` wires `get_document` to it (and can fall back seamlessly between them).
- Zero core code changes either way.

### 4.2 MCP Server Integration Contract (For the Regulatory Tool Team)

Our `MCPFCAHandbookAdapter` and `MCPConsumerCreditActAdapter` in `adapters/regulatory/mcp.py` support **both `stdio` and `streamable_http` MCP transports** via a declarative `MCPServerConfig` + `MCPToolMapping`:

```python
MCPServerConfig(
    transport="streamable_http",          # or "stdio"
    url="http://localhost:8001/mcp",      # for streamable_http
    # command="uv", args=["run", ...],    # for stdio
    headers={"Authorization": "Bearer ..."},
)
```

**Checklist to agree with the MCP team once their servers are ready:**
1. Transport type (`stdio` command/args vs `streamable_http` URL + auth headers).
2. Exact MCP tool name(s) for FCA Handbook RAG and Consumer Credit Act lookup/document fetch.
3. Input argument names (e.g., `query`, `top_k`, `sourcebook`, `section`).
4. Output payload shape (JSON list of objects vs plain text markdown) so our configurable response parser maps cleanly into `RegulatoryPassage(source, reference, title, text, url, score)`.

### 4.3 Pluggable I/O Adapter Contracts (`ports/adapters.py`)

Any callable (sync/async function) **or compiled LangGraph `StateGraph`** matching the `PipelineState` slice contract can be plugged in:

- **Inbound Adapter Node**: Reads `raw_input` (and optional `config`), returns `{"analysis_input": AnalysisInput, "adapter_context": dict}`.
- **Outbound Adapter Node / Subgraph**: Reads `findings_report`, `analysis_input`, `adapter_context`, and `human_review`, executes whatever deterministic or agentic tool calls it needs, and returns `{"deliverable_result": DeliverableResult}`.
- **`ContextModule`**: A small frozen bundle `(name, inbound_node, outbound_node, description)` representing one complete interchangeable I/O pairing.

---

## 5. Core LangGraph Subgraph & HITL Gate

### 5.1 Core Subgraph Nodes (`core/graph.py`)

1. **`triage_units`**: Evaluates each `AnalysisUnit` in `analysis_input.units` to filter out noise (e.g. formatting-only diffs, lockfiles, boilerplate legal disclaimers) and generate targeted FCA + CCA search queries per relevant unit.
2. **Parallel Fan-Out (`Send("analyze_unit", ...)`)**: LangGraph dispatches one isolated `UnitAnalysisState` branch per relevant unit in parallel:
   - Calls `FCAHandbookPort.search_rules(...)` and `ConsumerCreditActPort.query_provisions(...)` / `get_document(...)` concurrently (`asyncio.gather`).
   - Invokes `ComplianceAnalyzerPort.analyze_unit(unit, fca_passages, cca_passages)` to cross-check the artefact against both regulatory texts and produce grounded `Finding` items.
   - Results merge into `PipelineState` / `CoreAnalysisState` via an append reducer (`Annotated[list[Finding], operator.add]`).
3. **`synthesize_report`**: Deduplicates overlapping findings across units, sorts by severity (`critical` → `info`), assigns stable IDs (`F-001`, `F-002`, ...), computes the `overall_verdict`, and generates the `executive_summary` to emit the final `FindingsReport`.

### 5.2 Optional Human-in-the-Loop Gate (`human_review_gate`)

- Controlled by `PipelineConfig.require_human_approval: bool` (default `False`).
- When `False`: passes straight through to `outbound_adapter`.
- When `True`: calls LangGraph `interrupt(...)` with a concise review payload (verdict, executive summary, finding IDs/titles/severities).
- Resuming with `Command(resume=...)` supports:
  - `{"action": "approve", "notes": "..."}` — forward all findings to `outbound_adapter`.
  - `{"action": "filter", "approved_finding_ids": ["F-001"], "notes": "..."}` — filter `findings_report.findings` to only approved findings before invoking `outbound_adapter`.
  - `{"action": "reject", "notes": "..."}` — skip `outbound_adapter` and terminate cleanly with `DeliverableResult(status="aborted_by_reviewer")`.

---

## 6. Blueprints for Future I/O Modules

| Module | Inbound Adapter (Node/Subgraph) | `adapter_context` Passed Through | Outbound Adapter (Node/Subgraph) |
|---|---|---|---|
| **1. PR Code Review** | Runs `gh pr view --json ...` & `gh pr diff`; parses diff hunks into `AnalysisUnit`s with `PullRequestDiffLocation` | `{repo, pr_number, head_sha, pr_url}` | Formats `FindingsReport` into inline review comments anchored on `file_path` + `end_line` + summary review body via `gh api` / `gh pr review` |
| **2. Existing Codebase Review** | Clones default branch via `git clone --depth 1`; filters/chunks relevant source files into `AnalysisUnit`s with `CodeLocation` | `{repo_url, default_branch, workspace_path, base_sha}` | **Agentic Remediation Subgraph** (`StateGraph` with ReAct tool loop: `read_file`, `edit_file`, `run_tests`, `git_checkout_commit`, `gh pr create`) applying fixes for each `Finding` and opening a remediation PR |
| **3. Product Spec Review** | Extracts text & page/heading structure from incoming PDF into `AnalysisUnit`s with `DocumentLocation` | `{input_pdf_path, output_pdf_path}` | Renders `FindingsReport` (executive summary, severity matrix, FCA/CCA citation table, per-section recommendations) into a formatted PDF report |
