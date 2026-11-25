"""Composition root exposing ready-to-run compliance pipelines backed by real integrations.

Always wires real implementations:
  - LLM: `LangChainComplianceAnalyzer` (configured via repo root `.env`)
  - FCA Handbook RAG: `MCPFCAHandbookAdapter` over Streamable HTTP
    (`FCA_MCP_URL`, defaulting to the local `handbook_rag` server at `http://127.0.0.1:8765/mcp`)
  - Consumer Credit Act 1974: `MCPConsumerCreditActAdapter`
    (`CCA_MCP_URL` over Streamable HTTP, or local `fca-regulatory-mcp` over stdio backed by `cca.md`)
  - GitHub I/O: real `GitHubCLIAdapter` (`git` + `gh` CLI) for both `pr_review_agent` and
    `codebase_review_agent`
  - `product_spec_agent` reads a real PDF, Markdown or text specification

`compliance_agent` is the one graph that Studio shows: chat in front of all three.
"""

from __future__ import annotations

import os
import sys

from .adapters.chat import build_combined_graph
from .adapters.github_codebase import build_github_codebase_review_module
from .adapters.github_pr import build_github_pr_review_module
from .adapters.llm import LangChainComplianceAnalyzer
from .adapters.llm.langchain_llm import load_repo_env
from .adapters.product_spec import build_product_spec_review_module
from .adapters.regulatory import (
    MCPConsumerCreditActAdapter,
    MCPFCAHandbookAdapter,
    MCPServerConfig,
)
from .core import build_core_analysis_subgraph, build_pipeline_for_module
from .mcp_server.bootstrap import DEFAULT_FCA_MCP_URL
from .ports import ComplianceAnalyzerPort, ConsumerCreditActPort, FCAHandbookPort


def _default_stdio_cca_mcp_config() -> MCPServerConfig:
    return MCPServerConfig(
        transport="stdio",
        command=sys.executable,
        args=("-m", "fca_compliance_agent.mcp_server.server"),
    )


def build_default_core_subgraph(
    *,
    fca_port: FCAHandbookPort | None = None,
    cca_port: ConsumerCreditActPort | None = None,
    analyzer_port: ComplianceAnalyzerPort | None = None,
):
    """Wire the Core Analysis Subgraph from explicit ports or real environment config."""
    load_repo_env()

    if fca_port is None:
        fca_url = os.environ.get("FCA_MCP_URL") or DEFAULT_FCA_MCP_URL
        fca_port = MCPFCAHandbookAdapter(
            server_config=MCPServerConfig(transport="streamable_http", url=fca_url)
        )

    if cca_port is None:
        cca_url = os.environ.get("CCA_MCP_URL")
        if cca_url:
            cca_port = MCPConsumerCreditActAdapter(
                server_config=MCPServerConfig(transport="streamable_http", url=cca_url)
            )
        else:
            cca_port = MCPConsumerCreditActAdapter(
                server_config=_default_stdio_cca_mcp_config()
            )

    if analyzer_port is None:
        analyzer_port = LangChainComplianceAnalyzer()

    return build_core_analysis_subgraph(
        fca_port=fca_port,
        cca_port=cca_port,
        analyzer_port=analyzer_port,
    )


_default_core = build_default_core_subgraph()

pr_review_agent = build_pipeline_for_module(
    build_github_pr_review_module(),
    core_subgraph=_default_core,
)
codebase_review_agent = build_pipeline_for_module(
    build_github_codebase_review_module(),
    core_subgraph=_default_core,
)
product_spec_agent = build_pipeline_for_module(
    build_product_spec_review_module(),
    core_subgraph=_default_core,
)
compliance_agent = build_combined_graph(
    pr_review=pr_review_agent,
    codebase_review=codebase_review_agent,
    product_spec=product_spec_agent,
)
