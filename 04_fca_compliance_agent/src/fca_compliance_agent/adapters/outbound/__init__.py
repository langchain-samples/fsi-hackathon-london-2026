"""Outbound Adapters: API wrappers + Outbound Agents defining module reaction behaviour."""

from .code_remediator import (
    CodeRemediator,
    FileRemediationResult,
    LangChainCodeRemediator,
    make_code_remediation_outbound,
    render_codebase_markdown,
    render_remediation_pr_body,
)
from .github_client import GitHubCLIAdapter, GitHubClient, LocalMockGitHubClient, run_gh, run_git
from .stubs import (
    build_codebase_remediation_outbound_subgraph,
    build_codebase_review_module,
    build_pr_review_module,
    build_product_spec_module,
    pr_review_outbound_stub,
    product_spec_outbound_stub,
)

__all__ = [
    "CodeRemediator",
    "FileRemediationResult",
    "GitHubCLIAdapter",
    "GitHubClient",
    "LangChainCodeRemediator",
    "LocalMockGitHubClient",
    "build_codebase_remediation_outbound_subgraph",
    "build_codebase_review_module",
    "build_pr_review_module",
    "build_product_spec_module",
    "make_code_remediation_outbound",
    "pr_review_outbound_stub",
    "product_spec_outbound_stub",
    "render_codebase_markdown",
    "render_remediation_pr_body",
    "run_gh",
    "run_git",
]
