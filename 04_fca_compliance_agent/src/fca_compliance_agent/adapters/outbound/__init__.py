"""Outbound Adapters: API wrappers + Outbound Agents defining module reaction behaviour."""

from .code_remediator import (
    CodeRemediator,
    FileRemediationResult,
    LangChainCodeRemediator,
    make_code_remediation_outbound,
    render_codebase_markdown,
    render_remediation_pr_body,
)
from .github_client import GitHubCLIAdapter, GitHubClient, run_gh, run_git

__all__ = [
    "CodeRemediator",
    "FileRemediationResult",
    "GitHubCLIAdapter",
    "GitHubClient",
    "LangChainCodeRemediator",
    "make_code_remediation_outbound",
    "render_codebase_markdown",
    "render_remediation_pr_body",
    "run_gh",
    "run_git",
]
