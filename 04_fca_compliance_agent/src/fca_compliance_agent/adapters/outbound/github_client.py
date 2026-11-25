"""Git & GitHub CLI outbound API adapter.

Responsible solely for wrapping/abstracting `git` and `gh` CLI interactions
(cloning, branching, committing, pushing, opening PRs, and reading/commenting on PRs).
Contains no LLM reasoning or compliance behaviour.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote

import httpx

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

    @staticmethod
    def _github_api_headers() -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "fca-compliance-agent",
        }
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
        if token.strip():
            headers["Authorization"] = f"Bearer {token.strip()}"
        return headers

    def list_org_repos(self, org: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """List non-archived GitHub repositories under an organisation or user."""
        cleaned = org.strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", cleaned):
            raise ValueError(f"Invalid GitHub org/owner name: {org!r}")

        if shutil.which("gh"):
            try:
                raw = self._gh(
                    "repo",
                    "list",
                    cleaned,
                    "--limit",
                    str(limit),
                    "--json",
                    "nameWithOwner,url,defaultBranchRef,description,isPrivate,isArchived,pushedAt",
                )
                items = json.loads(raw)
                return [
                    {
                        "full_name": str(item.get("nameWithOwner") or ""),
                        "url": str(item.get("url") or f"https://github.com/{item.get('nameWithOwner')}"),
                        "default_branch": str((item.get("defaultBranchRef") or {}).get("name") or "main"),
                        "description": str(item.get("description") or ""),
                        "private": bool(item.get("isPrivate", False)),
                        "pushed_at": item.get("pushedAt"),
                    }
                    for item in items
                    if isinstance(item, dict) and not item.get("isArchived") and item.get("nameWithOwner")
                ]
            except (RuntimeError, ValueError, json.JSONDecodeError):
                pass

        headers = self._github_api_headers()
        with httpx.Client(timeout=20.0, headers=headers) as client:
            resp = client.get(
                f"https://api.github.com/orgs/{quote(cleaned, safe='')}/repos",
                params={"per_page": min(limit, 100), "type": "all", "sort": "updated"},
            )
            if resp.status_code == 404:
                resp = client.get(
                    f"https://api.github.com/users/{quote(cleaned, safe='')}/repos",
                    params={"per_page": min(limit, 100), "type": "all", "sort": "updated"},
                )
            resp.raise_for_status()
            items = resp.json()

        return [
            {
                "full_name": str(item.get("full_name") or ""),
                "url": str(item.get("html_url") or f"https://github.com/{item.get('full_name')}"),
                "default_branch": str(item.get("default_branch") or "main"),
                "description": str(item.get("description") or ""),
                "private": bool(item.get("private", False)),
                "pushed_at": item.get("pushed_at"),
            }
            for item in items
            if isinstance(item, dict) and not item.get("archived") and item.get("full_name")
        ]

    def list_repo_prs(
        self,
        repo_slug: str,
        *,
        state: str = "open",
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """List pull requests for a repository (`owner/repo`)."""
        cleaned = repo_slug.strip()
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", cleaned):
            raise ValueError(f"Invalid GitHub repository slug: {repo_slug!r}")
        norm_state = state.strip().lower()
        if norm_state not in {"open", "closed", "all"}:
            raise ValueError(f"Invalid PR state {state!r}; expected open, closed, or all.")

        if shutil.which("gh"):
            try:
                raw = self._gh(
                    "pr",
                    "list",
                    "--repo",
                    cleaned,
                    "--state",
                    norm_state,
                    "--limit",
                    str(limit),
                    "--json",
                    "number,title,url,state,headRefName,baseRefName,author,updatedAt",
                )
                items = json.loads(raw)
                return [
                    {
                        "number": int(item["number"]),
                        "title": str(item.get("title") or f"PR #{item['number']}"),
                        "url": str(item.get("url") or f"https://github.com/{cleaned}/pull/{item['number']}"),
                        "state": str(item.get("state") or norm_state).upper(),
                        "head_ref": str(item.get("headRefName") or ""),
                        "base_ref": str(item.get("baseRefName") or "main"),
                        "author": str((item.get("author") or {}).get("login") or ""),
                        "updated_at": item.get("updatedAt"),
                    }
                    for item in items
                    if isinstance(item, dict) and item.get("number") is not None
                ]
            except (RuntimeError, ValueError, json.JSONDecodeError):
                pass

        headers = self._github_api_headers()
        with httpx.Client(timeout=20.0, headers=headers) as client:
            resp = client.get(
                f"https://api.github.com/repos/{cleaned}/pulls",
                params={"state": norm_state, "per_page": min(limit, 100)},
            )
            resp.raise_for_status()
            items = resp.json()

        return [
            {
                "number": int(item["number"]),
                "title": str(item.get("title") or f"PR #{item['number']}"),
                "url": str(item.get("html_url") or f"https://github.com/{cleaned}/pull/{item['number']}"),
                "state": str(item.get("state") or norm_state).upper(),
                "head_ref": str((item.get("head") or {}).get("ref") or ""),
                "base_ref": str((item.get("base") or {}).get("ref") or "main"),
                "author": str((item.get("user") or {}).get("login") or ""),
                "updated_at": item.get("updated_at"),
            }
            for item in items
            if isinstance(item, dict) and item.get("number") is not None
        ]
