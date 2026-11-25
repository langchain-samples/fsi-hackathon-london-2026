"""Git & GitHub CLI outbound API adapter.

Responsible solely for wrapping/abstracting `git` and `gh` CLI interactions
(cloning, branching, committing, pushing, opening PRs, and reading/commenting on PRs).
Contains no LLM reasoning or compliance behaviour.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

_GIT_AUTH_ERROR_HINTS = (
    "authentication failed",
    "could not read username",
    "terminal prompts disabled",
    "permission denied",
    "requested url returned error: 403",
    "invalid username or token",
)


def run_git(*args: str, cwd: Path | None = None) -> str:
    """Run `git` with list args (no shell, preventing command injection)."""
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    done = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env=env,
    )
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()[:400]
        raise RuntimeError(f"git {args[0]} failed: {detail}")
    return done.stdout


def run_gh(*args: str, cwd: Path | None = None) -> str:
    """Run `gh` with list args (no shell, preventing command injection)."""
    done = subprocess.run(
        ["gh", *args],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip()[:400]
        subcmd = " ".join(args[:2]) if len(args) >= 2 else (args[0] if args else "")
        raise RuntimeError(f"gh {subcmd} failed: {detail}")
    return done.stdout


@runtime_checkable
class GitHubClient(Protocol):
    """Protocol abstracting Git and GitHub PR operations for outbound/inbound adapters."""

    def clone_repo(self, repo_url: str, branch: str, dest: Path) -> None:
        ...

    def checkout_new_branch(self, repo_dir: Path, branch_name: str) -> None:
        ...

    def commit_and_push(
        self,
        repo_dir: Path,
        branch_name: str,
        files: list[str],
        commit_message: str,
    ) -> None:
        ...

    def create_pr(
        self,
        *,
        repo_slug: str,
        base_branch: str,
        head_branch: str,
        title: str,
        body_file: Path,
        draft: bool = False,
        cwd: Path | None = None,
    ) -> str:
        ...

    def view_pr(self, repo_slug: str, pr_number: int) -> dict[str, Any]:
        ...

    def diff_pr(self, repo_slug: str, pr_number: int) -> str:
        ...

    def comment_pr(self, repo_slug: str, pr_number: int, body_file: Path) -> str:
        ...


class GitHubCLIAdapter:
    """Production `GitHubClient` backed by local `git` and `gh` CLI executables."""

    def __init__(self, *, git_runner=run_git, gh_runner=run_gh) -> None:
        self._git = git_runner
        self._gh = gh_runner

    def clone_repo(self, repo_url: str, branch: str, dest: Path) -> None:
        clone_url = repo_url if repo_url.endswith(".git") else f"{repo_url}.git"
        self._git("clone", "--depth", "1", "--branch", branch, clone_url, str(dest))

    def checkout_new_branch(self, repo_dir: Path, branch_name: str) -> None:
        self._git("checkout", "-b", branch_name, cwd=repo_dir)

    def _ensure_git_identity(self, repo_dir: Path) -> None:
        """Ensure the temporary clone has a local author identity without touching global git config."""
        for key, fallback in (
            ("user.name", "FCA Compliance Agent"),
            ("user.email", "fca-compliance-agent@users.noreply.github.com"),
        ):
            try:
                existing = self._git("config", "--get", key, cwd=repo_dir).strip()
            except RuntimeError:
                existing = ""
            if not existing:
                self._git("config", key, fallback, cwd=repo_dir)

    def _push_with_gh_fallback(self, repo_dir: Path, branch_name: str) -> None:
        try:
            self._git("push", "-u", "origin", branch_name, cwd=repo_dir)
        except RuntimeError as exc:
            lowered = str(exc).lower()
            if any(hint in lowered for hint in _GIT_AUTH_ERROR_HINTS) and shutil.which("gh"):
                self._git(
                    "-c",
                    "credential.helper=!gh auth git-credential",
                    "push",
                    "-u",
                    "origin",
                    branch_name,
                    cwd=repo_dir,
                )
                return
            raise

    def commit_and_push(
        self,
        repo_dir: Path,
        branch_name: str,
        files: list[str],
        commit_message: str,
    ) -> None:
        self._ensure_git_identity(repo_dir)
        self._git("add", "--", *files, cwd=repo_dir)
        self._git("commit", "-m", commit_message, cwd=repo_dir)
        self._push_with_gh_fallback(repo_dir, branch_name)

    def create_pr(
        self,
        *,
        repo_slug: str,
        base_branch: str,
        head_branch: str,
        title: str,
        body_file: Path,
        draft: bool = False,
        cwd: Path | None = None,
    ) -> str:
        args = [
            "pr",
            "create",
            "--repo",
            repo_slug,
            "--base",
            base_branch,
            "--head",
            head_branch,
            "--title",
            title,
            "--body-file",
            str(body_file),
        ]
        if draft:
            args.append("--draft")
        return self._gh(*args, cwd=cwd).strip()

    def view_pr(self, repo_slug: str, pr_number: int) -> dict[str, Any]:
        raw = self._gh(
            "pr",
            "view",
            str(pr_number),
            "--repo",
            repo_slug,
            "--json",
            "title,body,url,headRefOid",
        )
        return json.loads(raw)

    def diff_pr(self, repo_slug: str, pr_number: int) -> str:
        return self._gh("pr", "diff", str(pr_number), "--repo", repo_slug)

    def comment_pr(self, repo_slug: str, pr_number: int, body_file: Path) -> str:
        return self._gh(
            "pr",
            "comment",
            str(pr_number),
            "--repo",
            repo_slug,
            "--body-file",
            str(body_file),
        ).strip()
