"""Codebase Review module wiring (Inbound repository scanner + Outbound code remediation agent).

Inbound  `raw_input` -> `analysis_input` (one AnalysisUnit per source/config file) + `adapter_context`
    {"repo_url": "https://github.com/owner/repo", "default_branch": "main"}   shallow-clone via `GitHubClient`
    {"repo": "owner/repo", "default_branch": "main"}                          same, without full URL
    {"workspace_path": "/path/to/local/repo"}                                 offline / pre-cloned dir

Outbound `findings_report` -> handled by `adapters/outbound/code_remediator.py` (which uses
    `adapters/outbound/github_client.py` to branch, commit, push, and open a remediation PR).
    `raw_input["output_type"]` is "remediation_pr" (the default for a GitHub repo) or "report", which only saves local
    reports. `raw_input["create_pr"]` is the older way to say the same.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any, Literal

from ..domain.models import AnalysisInput, AnalysisUnit, CodeLocation
from ..domain.state import PipelineState
from ..ports.adapters import ContextModule
from .outbound.code_remediator import (
    REPORTS_DIR,
    CodeRemediator,
    FileRemediationResult,
    LangChainCodeRemediator,
    make_code_remediation_outbound,
    render_codebase_markdown,
    render_remediation_pr_body,
)
from .outbound.github_client import GitHubCLIAdapter, GitHubClient, run_gh, run_git
from .output_type import decide_output_type

_REPO_URL = re.compile(r"^https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?$")
_REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
_BRANCH = re.compile(r"^[\w./-]+$")
_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
    }
)
_SKIP_FILES = re.compile(
    r"(\.lock|lock\.json|\.png|\.jpe?g|\.gif|\.pdf|\.ico|\.svg|\.woff2?|\.ttf|\.eot|\.zip|\.tar|\.gz|\.pyc)$"
    r"|(^|/)(uv|poetry|yarn)\.lock$",
    re.IGNORECASE,
)
_CODE_EXTS = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".java",
        ".kt",
        ".go",
        ".rs",
        ".rb",
        ".cs",
        ".php",
        ".sql",
        ".sh",
    }
)
_CONFIG_EXTS = frozenset({".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".txt", ".xml"})
MAX_FILE_CHARS = 12_000
MAX_FILES = 100

# Module-level aliases preserved for convenience/testing.
_git = run_git
_gh = run_gh


def _resolve_repo(raw: dict[str, Any]) -> tuple[str, str, str]:
    """Return `(repo_slug, repo_url, branch)` validated against safe patterns."""
    branch = str(raw.get("default_branch") or "main").strip()
    if not _BRANCH.match(branch) or ".." in branch or branch.startswith("-"):
        raise ValueError(f"Invalid branch name: {branch!r}")

    if url := raw.get("repo_url"):
        m = _REPO_URL.match(str(url).strip())
        if not m:
            raise ValueError(f"Not a GitHub repository URL: {url!r}")
        slug = m.group(1)
        return slug, f"https://github.com/{slug}", branch

    repo = str(raw.get("repo", "")).strip()
    if not _REPO.match(repo):
        raise ValueError(
            "Give raw_input 'workspace_path' (local dir), 'repo_url' (https://github.com/owner/name), "
            "or 'repo' (owner/name)."
        )
    return repo, f"https://github.com/{repo}", branch


def _resolve_optional_repo(raw: dict[str, Any]) -> tuple[str | None, str | None, str]:
    """Resolve optional GitHub repo metadata when `workspace_path` is provided."""
    branch = str(raw.get("default_branch") or "main").strip()
    if not _BRANCH.match(branch) or ".." in branch or branch.startswith("-"):
        raise ValueError(f"Invalid branch name: {branch!r}")
    if raw.get("repo_url") or raw.get("repo"):
        slug, repo_url, branch = _resolve_repo(raw)
        return slug, repo_url, branch
    return None, None, branch


def _is_binary(path: Path) -> bool:
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return True
    return b"\x00" in chunk


def _classify_content_type(path: Path) -> Literal["code", "config", "other"]:
    ext = path.suffix.lower()
    if ext in _CODE_EXTS:
        return "code"
    if ext in _CONFIG_EXTS:
        return "config"
    return "other"


def collect_codebase_units(repo_dir: Path) -> tuple[list[AnalysisUnit], str, int]:
    """Walk `repo_dir` and return `(units, readme_summary, skipped_count)`."""
    readme_summary = ""
    for candidate in ("README.md", "readme.md", "README.txt"):
        readme_path = repo_dir / candidate
        if readme_path.is_file():
            readme_summary = readme_path.read_text(encoding="utf-8", errors="replace")[:2000]
            break

    units: list[AnalysisUnit] = []
    skipped = 0
    for path in sorted(repo_dir.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(repo_dir).parts
        if any(part in _SKIP_DIRS for part in rel_parts):
            continue
        rel = path.relative_to(repo_dir).as_posix()
        if _SKIP_FILES.search(rel) or _is_binary(path):
            skipped += 1
            continue
        if len(units) >= MAX_FILES:
            skipped += 1
            continue

        raw_text = path.read_text(encoding="utf-8", errors="replace")
        line_count = max(1, len(raw_text.splitlines()))
        content = raw_text if len(raw_text) <= MAX_FILE_CHARS else raw_text[:MAX_FILE_CHARS] + "\n... [truncated]"
        units.append(
            AnalysisUnit(
                unit_id=f"file-{len(units) + 1}",
                title=rel,
                content=content,
                content_type=_classify_content_type(path),
                location=CodeLocation(file_path=rel, start_line=1, end_line=line_count),
            )
        )
    return units, readme_summary, skipped


def make_github_codebase_inbound(*, github_client: GitHubClient | None = None):
    """Build the inbound node that scans a local directory or clones a GitHub repo."""

    def github_codebase_inbound(state: PipelineState) -> dict[str, Any]:
        raw = state.get("raw_input") or {}
        local_ws = raw.get("workspace_path")
        output_type = decide_output_type("codebase_review", raw)

        if local_ws and Path(str(local_ws)).is_dir():
            repo_dir = Path(str(local_ws)).resolve()
            slug, parsed_url, branch = _resolve_optional_repo(raw)
            repo_url = parsed_url or str(repo_dir)
            units, readme_summary, skipped = collect_codebase_units(repo_dir)
            cloned = False
            workspace_label = str(repo_dir)
        else:
            slug, repo_url, branch = _resolve_repo(raw)
            gh_client = github_client or GitHubCLIAdapter(git_runner=_git, gh_runner=_gh)
            with tempfile.TemporaryDirectory(prefix="fca-codebase-") as tmpdir:
                clone_dir = Path(tmpdir) / "repo"
                gh_client.clone_repo(repo_url, branch, clone_dir)
                units, readme_summary, skipped = collect_codebase_units(clone_dir)
            cloned = True
            workspace_label = f"ephemeral:{slug}@{branch}"

        draft_pr = bool(raw.get("draft_pr", False))
        summary = str(raw.get("summary") or readme_summary or f"Codebase compliance review of {repo_url} ({branch}).")
        analysis_input = AnalysisInput(
            target_id=repo_url,
            source_type="codebase",
            title=str(raw.get("title") or f"Codebase compliance scan: {repo_url} ({branch})"),
            summary=summary[:2000],
            units=units,
        )
        return {
            "analysis_input": analysis_input,
            "adapter_context": {
                "repo_slug": slug,
                "repo_url": repo_url,
                "default_branch": branch,
                "workspace_path": workspace_label,
                "cloned_remotely": cloned,
                "files_skipped": skipped,
                "draft_pr": draft_pr,
                "summary": summary[:2000],
            },
            "output_type": output_type,
        }

    return github_codebase_inbound


github_codebase_inbound = make_github_codebase_inbound()
github_codebase_outbound = make_code_remediation_outbound()


def build_github_codebase_review_module(
    *,
    remediator: CodeRemediator | None = None,
    github_client: GitHubClient | None = None,
    reports_dir: Path | None = None,
) -> ContextModule:
    """Wire the Codebase Review inbound scanner and outbound remediation agent + GitHub adapter."""
    client = github_client or GitHubCLIAdapter(git_runner=_git, gh_runner=_gh)
    return ContextModule(
        name="codebase_review",
        description="GitHub repository (or local workspace) -> markdown/JSON report + code remediation PR",
        inbound_node=make_github_codebase_inbound(github_client=client),
        outbound_node=make_code_remediation_outbound(
            remediator=remediator,
            github_client=client,
            reports_dir=reports_dir or REPORTS_DIR,
        ),
    )


__all__ = [
    "CodeRemediator",
    "FileRemediationResult",
    "GitHubCLIAdapter",
    "GitHubClient",
    "LangChainCodeRemediator",
    "REPORTS_DIR",
    "build_github_codebase_review_module",
    "collect_codebase_units",
    "github_codebase_inbound",
    "github_codebase_outbound",
    "make_github_codebase_inbound",
    "render_codebase_markdown",
    "render_remediation_pr_body",
]
