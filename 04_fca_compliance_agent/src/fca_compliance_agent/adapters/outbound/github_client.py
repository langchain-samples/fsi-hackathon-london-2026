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


class LocalMockGitHubClient:
    """Mock `GitHubClient` API adapter that simulates GitHub on local disk and reports to stdout.

    Use this in tests and offline dry-runs so the REAL inbound/outbound behaviour adapters
    (diff splitting, code remediation, PR body rendering, HITL gates) run end-to-end while
    only the external GitHub API/CLI boundary is mocked.
    """

    def __init__(
        self,
        *,
        output_dir: Path,
        repo_files: dict[str, str] | None = None,
        pr_meta: dict[str, Any] | None = None,
        pr_diff: str = "",
        fail_on_create: str | None = None,
        fail_on_comment: str | None = None,
        report_to_stdout: bool = True,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.repo_files = dict(repo_files or {})
        self.pr_meta = dict(pr_meta or {})
        self.pr_diff = pr_diff
        self.fail_on_create = fail_on_create
        self.fail_on_comment = fail_on_comment
        self.report_to_stdout = report_to_stdout

        self.cloned: list[dict[str, Any]] = []
        self.branches: list[str] = []
        self.commits: list[dict[str, Any]] = []
        self.created_prs: list[dict[str, Any]] = []
        self.viewed_prs: list[tuple[str, int]] = []
        self.diffed_prs: list[tuple[str, int]] = []
        self.posted_comments: list[dict[str, Any]] = []
        self.written_paths: list[Path] = []

    def _emit(self, message: str) -> None:
        if self.report_to_stdout:
            print(message)

    def _record_file(self, path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self.written_paths.append(path)
        return path

    def clone_repo(self, repo_url: str, branch: str, dest: Path) -> None:
        dest.mkdir(parents=True, exist_ok=True)
        for rel, text in self.repo_files.items():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        record = {"repo_url": repo_url, "branch": branch, "files": sorted(self.repo_files.keys())}
        self.cloned.append(record)
        self._emit(
            f"[MockGitHubAPI] Cloned {repo_url}@{branch} ({len(self.repo_files)} file(s)) -> {dest}"
        )

    def checkout_new_branch(self, repo_dir: Path, branch_name: str) -> None:
        self.branches.append(branch_name)
        self._emit(f"[MockGitHubAPI] Checked out branch '{branch_name}'")

    def commit_and_push(
        self,
        repo_dir: Path,
        branch_name: str,
        files: list[str],
        commit_message: str,
    ) -> None:
        safe_branch = branch_name.replace("/", "__")
        branch_dir = self.output_dir / "branches" / safe_branch
        written_files: dict[str, str] = {}
        disk_files: dict[str, str] = {}
        for rel in files:
            content = (repo_dir / rel).read_text(encoding="utf-8")
            written_files[rel] = content
            saved = self._record_file(branch_dir / rel, content)
            disk_files[rel] = str(saved)

        meta_path = self._record_file(
            branch_dir / "_commit.json",
            json.dumps(
                {
                    "branch": branch_name,
                    "files": list(files),
                    "commit_message": commit_message,
                    "disk_files": disk_files,
                },
                indent=2,
            ),
        )
        self.commits.append(
            {
                "branch": branch_name,
                "files": list(files),
                "commit_message": commit_message,
                "written_files": written_files,
                "meta_path": str(meta_path),
            }
        )
        self._emit(
            f"[MockGitHubAPI] Pushed branch '{branch_name}' ({len(files)} file(s)) -> {branch_dir}"
        )

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
        if self.fail_on_create:
            raise RuntimeError(self.fail_on_create)
        pr_number = len(self.created_prs) + 1
        pr_url = f"https://github.com/{repo_slug}/pull/{pr_number}"
        body = body_file.read_text(encoding="utf-8")

        pr_dir = self.output_dir / "prs"
        md_path = self._record_file(pr_dir / f"pr_{pr_number}.md", body)
        meta = {
            "pr_number": pr_number,
            "pr_url": pr_url,
            "repo_slug": repo_slug,
            "base_branch": base_branch,
            "head_branch": head_branch,
            "title": title,
            "draft": draft,
            "body_path": str(md_path),
        }
        json_path = self._record_file(pr_dir / f"pr_{pr_number}.json", json.dumps(meta, indent=2))
        self.created_prs.append({**meta, "body": body, "meta_path": str(json_path)})
        self._emit(
            f"[MockGitHubAPI] Opened {'draft ' if draft else ''}PR {pr_url} "
            f"({head_branch} -> {base_branch}) | saved -> {md_path}"
        )
        return pr_url

    def view_pr(self, repo_slug: str, pr_number: int) -> dict[str, Any]:
        self.viewed_prs.append((repo_slug, pr_number))
        meta = {
            "title": f"PR #{pr_number}",
            "body": "",
            "url": f"https://github.com/{repo_slug}/pull/{pr_number}",
            "headRefOid": "mockheadsha",
            **self.pr_meta,
        }
        self._emit(f"[MockGitHubAPI] Viewed PR {repo_slug}#{pr_number} ({meta['title']})")
        return meta

    def diff_pr(self, repo_slug: str, pr_number: int) -> str:
        self.diffed_prs.append((repo_slug, pr_number))
        self._emit(
            f"[MockGitHubAPI] Fetched diff for {repo_slug}#{pr_number} ({len(self.pr_diff)} chars)"
        )
        return self.pr_diff

    def comment_pr(self, repo_slug: str, pr_number: int, body_file: Path) -> str:
        if self.fail_on_comment:
            raise RuntimeError(self.fail_on_comment)
        comment_id = len(self.posted_comments) + 1
        comment_url = f"https://github.com/{repo_slug}/pull/{pr_number}#issuecomment-{comment_id}"
        body = body_file.read_text(encoding="utf-8")

        comments_dir = self.output_dir / "comments"
        md_path = self._record_file(comments_dir / f"pr_{pr_number}_comment_{comment_id}.md", body)
        meta = {
            "comment_id": comment_id,
            "comment_url": comment_url,
            "repo_slug": repo_slug,
            "pr_number": pr_number,
            "body_path": str(md_path),
        }
        json_path = self._record_file(
            comments_dir / f"pr_{pr_number}_comment_{comment_id}.json",
            json.dumps(meta, indent=2),
        )
        self.posted_comments.append({**meta, "body": body, "meta_path": str(json_path)})
        self._emit(
            f"[MockGitHubAPI] Posted PR comment {comment_url} | saved -> {md_path}"
        )
        return comment_url

    def format_summary(self) -> str:
        lines = [
            f"[MockGitHubAPI Summary] output_dir={self.output_dir}",
            f"  Clones:   {len(self.cloned)}",
            f"  Commits:  {len(self.commits)}",
            f"  PRs:      {len(self.created_prs)}",
            f"  Comments: {len(self.posted_comments)}",
        ]
        for path in self.written_paths:
            lines.append(f"  Artifact: {path}")
        return "\n".join(lines)

    def print_summary(self) -> str:
        summary = self.format_summary()
        self._emit(summary)
        return summary
