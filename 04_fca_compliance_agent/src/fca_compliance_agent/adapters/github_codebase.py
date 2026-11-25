"""Real Inbound & Outbound adapters for the Codebase Review module (GitHub repo or local directory).

Inbound  `raw_input` -> `analysis_input` (one AnalysisUnit per source/config file) + `adapter_context`
    {"repo_url": "https://github.com/owner/repo", "default_branch": "main"}   shallow-clone via `git`
    {"repo": "owner/repo", "default_branch": "main"}                          same, without full URL
    {"workspace_path": "/path/to/local/repo"}                                 offline / pre-cloned dir

Outbound `findings_report` -> markdown + JSON review in `04_fca_compliance_agent/reports/`.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

from ..domain.models import (
    AnalysisInput,
    AnalysisUnit,
    CodeLocation,
    DeliverableResult,
    FindingsReport,
    PullRequestDiffLocation,
)
from ..domain.state import PipelineState
from ..ports.adapters import ContextModule

REPORTS_DIR = Path(__file__).resolve().parents[3] / "reports"  # 04_fca_compliance_agent/reports

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


def _git(*args: str) -> str:
    """Run `git` with list args (no shell, preventing command injection)."""
    done = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", timeout=120)
    if done.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {done.stderr.strip()[:300]}")
    return done.stdout


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


def github_codebase_inbound(state: PipelineState) -> dict[str, Any]:
    """Inbound adapter: local workspace dir or GitHub repo URL -> normalised AnalysisInput."""
    raw = state.get("raw_input") or {}
    local_ws = raw.get("workspace_path")
    branch = str(raw.get("default_branch") or "main").strip()

    if local_ws and Path(str(local_ws)).is_dir():
        repo_dir = Path(str(local_ws)).resolve()
        repo_url = str(raw.get("repo_url") or str(repo_dir))
        units, readme_summary, skipped = collect_codebase_units(repo_dir)
        cloned = False
        workspace_label = str(repo_dir)
    else:
        slug, repo_url, branch = _resolve_repo(raw)
        with tempfile.TemporaryDirectory(prefix="fca-codebase-") as tmpdir:
            clone_dir = Path(tmpdir) / "repo"
            _git("clone", "--depth", "1", "--branch", branch, f"{repo_url}.git", str(clone_dir))
            units, readme_summary, skipped = collect_codebase_units(clone_dir)
        cloned = True
        workspace_label = f"ephemeral:{slug}@{branch}"

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
            "repo_url": repo_url,
            "default_branch": branch,
            "workspace_path": workspace_label,
            "cloned_remotely": cloned,
            "files_skipped": skipped,
        },
    }


def render_codebase_markdown(report: FindingsReport) -> str:
    lines = [
        f"## FCA / CCA codebase compliance review: {report.title}",
        "",
        f"**Verdict:** `{report.overall_verdict}` · **Findings:** {len(report.findings)} · "
        f"**Files reviewed:** {report.units_triaged_in}/{report.units_total}",
        "",
        report.executive_summary,
        "",
    ]
    for f in report.findings:
        loc = f.location
        if isinstance(loc, (CodeLocation, PullRequestDiffLocation)):
            where = f"`{loc.file_path}` L{loc.start_line}-{loc.end_line}"
        else:
            where = ""
        refs = [c.reference for c in f.fca_citations + f.cca_citations]
        lines += [
            f"### [{f.severity.upper()}] {f.title}",
            f"{where} · status `{f.status}` · confidence {f.confidence:.0%}",
            "",
            f.description,
            "",
            f"- **Rules:** {', '.join(refs) or 'none cited'}",
            f"- **Fix:** {f.remediation_guidance}",
        ]
        if f.suggested_change:
            lines += ["", "```", f.suggested_change, "```"]
        lines.append("")
    lines.append("_Automated AI review. Not legal, compliance or regulatory advice; a human must confirm._")
    return "\n".join(lines)


def github_codebase_outbound(state: PipelineState) -> dict[str, Any]:
    """Outbound adapter: write markdown + JSON compliance report to `reports/`."""
    report = state["findings_report"]
    ctx = state.get("adapter_context") or {}
    REPORTS_DIR.mkdir(exist_ok=True)
    md_path = REPORTS_DIR / f"{report.report_id}.md"
    json_path = REPORTS_DIR / f"{report.report_id}.json"

    md_path.write_text(render_codebase_markdown(report), encoding="utf-8")
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    artifacts = [str(md_path), str(json_path)]
    files_with_findings = list(
        dict.fromkeys(
            getattr(f.location, "file_path", "")
            for f in report.findings
            if getattr(f.location, "file_path", "")
        )
    )

    return {
        "deliverable_result": DeliverableResult(
            module_name="codebase_review",
            status="completed",
            summary=(
                f"{report.overall_verdict}: {len(report.findings)} finding(s) across "
                f"{len(files_with_findings)} file(s) for {report.target_id}. Saved local report."
            ),
            artifacts=artifacts,
            metadata={
                "repo_url": ctx.get("repo_url"),
                "default_branch": ctx.get("default_branch"),
                "cloned_remotely": ctx.get("cloned_remotely", False),
                "files_with_findings": files_with_findings,
                "severity_counts": report.severity_counts(),
            },
        )
    }


def build_github_codebase_review_module() -> ContextModule:
    return ContextModule(
        name="codebase_review",
        description="GitHub repository (or local workspace) -> markdown/JSON compliance findings report",
        inbound_node=github_codebase_inbound,
        outbound_node=github_codebase_outbound,
    )
