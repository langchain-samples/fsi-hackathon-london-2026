"""Composition root exposing ready-to-run compliance pipelines for all 3 context modules.

Uses `LangChainComplianceAnalyzer` (configured via the repo root `.env`) by default.
Regulatory tools default to the in-memory mock FCA/CCA adapters until external MCP
servers are configured via environment variables:
  - `FCA_MCP_URL=http://...` -> uses `MCPFCAHandbookAdapter` over Streamable HTTP
  - `CCA_MCP_URL=http://...` -> uses `MCPConsumerCreditActAdapter` over Streamable HTTP
"""

from __future__ import annotations

import os

from .adapters.github_codebase import build_github_codebase_review_module
from .adapters.github_pr import build_github_pr_review_module
from .adapters.llm import LangChainComplianceAnalyzer
from .adapters.llm.langchain_llm import load_repo_env
from .adapters.outbound import (
    build_product_spec_module,
)
from .adapters.regulatory import (
    MCPConsumerCreditActAdapter,
    MCPFCAHandbookAdapter,
    MCPServerConfig,
    MockConsumerCreditActAdapter,
    MockFCAHandbookAdapter,
)
from .core import build_core_analysis_subgraph, build_pipeline_for_module
from .ports import ComplianceAnalyzerPort, ConsumerCreditActPort, FCAHandbookPort


def build_default_core_subgraph(
    *,
    fca_port: FCAHandbookPort | None = None,
    cca_port: ConsumerCreditActPort | None = None,
    analyzer_port: ComplianceAnalyzerPort | None = None,
):
    """Wire the Core Analysis Subgraph from explicit ports or environment config."""
    load_repo_env()

    if fca_port is None:
        fca_url = os.environ.get("FCA_MCP_URL")
        fca_port = (
            MCPFCAHandbookAdapter(
                server_config=MCPServerConfig(transport="streamable_http", url=fca_url)
            )
            if fca_url
            else MockFCAHandbookAdapter()
        )

    if cca_port is None:
        cca_url = os.environ.get("CCA_MCP_URL")
        cca_port = (
            MCPConsumerCreditActAdapter(
                server_config=MCPServerConfig(transport="streamable_http", url=cca_url)
            )
            if cca_url
            else MockConsumerCreditActAdapter()
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
    build_product_spec_module(),
    core_subgraph=_default_core,
)
