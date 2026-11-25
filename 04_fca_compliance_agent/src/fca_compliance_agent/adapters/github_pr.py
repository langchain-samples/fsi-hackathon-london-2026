"""Real Inbound & Outbound adapters for the PR Review module (GitHub via the `gh` CLI).

Inbound  `raw_input` -> `analysis_input` (one AnalysisUnit per changed file) + `adapter_context`
    {"pr_url": "https://github.com/owner/repo/pull/12"}      read a real PR with `gh`
    {"repo": "owner/repo", "pr_number": 12}                  same, without a URL
    {"diff": "<unified diff text>", "title": "..."}          offline / synthetic PRs, no GitHub call

Outbound `findings_report` -> markdown + JSON review in `reports/`, and optionally ONE PR comment.
    Posting is off by default. It only happens when `raw_input["post_to_github"]` is true AND the
    pipeline ran with `require_human_approval=True` AND a human approved (or filtered) the findings.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from ..domain.models import (
    AnalysisInput,
    AnalysisUnit,
    DeliverableResult,
    FindingsReport,
    PullRequestDiffLocation,
)
from ..domain.state import PipelineState
from ..ports.adapters import ContextModule

REPORTS_DIR = Path(__file__).resolve().parents[3] / "reports"  # 04_fca_compliance_agent/reports

_PR_URL = re.compile(r"^https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)/?$")
_REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_SKIP = re.compile(r"(\.lock|lock\.json|\.png|\.jpe?g|\.gif|\.pdf|\.ico|\.svg)$|(^|/)(uv|poetry|yarn)\.lock$")
MAX_FILE_CHARS = 12_000  # ponytail: hard truncation per file; split into hunks if big PRs miss findings


def _gh(*args: str) -> str:
    """Run `gh` with list args (no shell, so PR data can't inject commands)."""
    done = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", timeout=60)
    if done.returncode != 0:
        raise RuntimeError(f"gh {args[0]} {args[1]} failed: {done.stderr.strip()[:300]}")
    return done.stdout


def _resolve_pr(raw: dict[str, Any]) -> tuple[str, int]:
    if url := raw.get("pr_url"):
        m = _PR_URL.match(str(url).strip())
        if not m:
            raise ValueError(f"Not a GitHub PR URL: {url!r}")
        return m.group(1), int(m.group(2))
    repo, number = str(raw.get("repo", "")), raw.get("pr_number")
    if not _REPO.match(repo) or not str(number).isdigit():
        raise ValueError("Give raw_input 'pr_url', or 'repo' (owner/name) + 'pr_number', or 'diff'.")
    return repo, int(number)


def split_diff(diff: str) -> list[AnalysisUnit]:
    """One AnalysisUnit per changed file, anchored to the new-file line range it touches."""
    units: list[AnalysisUnit] = []
    for block in re.split(r"^(?=diff --git )", diff, flags=re.M):
        header = re.match(r"diff --git a/(\S+) b/(\S+)", block)
        if not header:
            continue
        path = header.group(2)
        if _SKIP.search(path) or "Binary files" in block:
            continue
        starts, ends, first_hunk = [], [], None
        for line in block.splitlines():
            if h := _HUNK.match(line):
                start, length = int(h.group(1)), int(h.group(2) or 1)
                starts.append(start)
                ends.append(start + max(length - 1, 0))
                first_hunk = first_hunk or line
        if not starts:  # renames / mode changes only
            continue
        content = block if len(block) <= MAX_FILE_CHARS else block[:MAX_FILE_CHARS] + "\n... [truncated]"
        units.append(
            AnalysisUnit(
                unit_id=f"file-{len(units) + 1}",
                title=path,
                content=content,
                content_type="diff",
                location=PullRequestDiffLocation(
                    file_path=path, start_line=min(starts), end_line=max(ends), hunk_header=first_hunk
                ),
            )
        )
    return units


def github_pr_inbound(state: PipelineState) -> dict[str, Any]:
    """Inbound adapter: a GitHub PR (or a raw diff) -> normalised AnalysisInput."""
    raw = state.get("raw_input") or {}
    if raw.get("diff"):
        repo, number = str(raw.get("repo", "local/synthetic")), int(raw.get("pr_number", 0))
        diff = str(raw["diff"])
        meta = {"title": raw.get("title", "Synthetic PR"), "body": raw.get("summary", ""), "url": None}
    else:
        repo, number = _resolve_pr(raw)
        meta = json.loads(_gh("pr", "view", str(number), "--repo", repo, "--json", "title,body,url,headRefOid"))
        diff = _gh("pr", "diff", str(number), "--repo", repo)

    units = split_diff(diff)
    analysis_input = AnalysisInput(
        target_id=f"{repo}#{number}",
        source_type="pr_review",
        title=str(meta.get("title") or f"PR #{number}"),
        # PR description is author-controlled: the core prompt sees it as context, not instructions.
        summary=str(meta.get("body") or "")[:2000],
        units=units,
    )
    return {
        "analysis_input": analysis_input,
        "adapter_context": {
            "repo": repo,
            "pr_number": number,
            "pr_url": meta.get("url"),
            "head_sha": meta.get("headRefOid"),
            "post_to_github": bool(raw.get("post_to_github", False)),
            "files_skipped": diff.count("diff --git ") - len(units),
        },
    }


def render_markdown(report: FindingsReport) -> str:
    lines = [
        f"## FCA / CCA compliance review: {report.title}",
        "",
        f"**Verdict:** `{report.overall_verdict}` · **Findings:** {len(report.findings)} · "
        f"**Files reviewed:** {report.units_triaged_in}/{report.units_total}",
        "",
        report.executive_summary,
        "",
    ]
    for f in report.findings:
        loc = f.location
        where = f"`{loc.file_path}` L{loc.start_line}-{loc.end_line}" if isinstance(loc, PullRequestDiffLocation) else ""
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


def github_pr_outbound(state: PipelineState) -> dict[str, Any]:
    """Outbound adapter: write the review to reports/, and post one PR comment only if approved."""
    report = state["findings_report"]
    ctx = state.get("adapter_context") or {}
    REPORTS_DIR.mkdir(exist_ok=True)
    md_path = REPORTS_DIR / f"{report.report_id}.md"
    json_path = REPORTS_DIR / f"{report.report_id}.json"
    body = render_markdown(report)
    md_path.write_text(body, encoding="utf-8")
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    artifacts = [str(md_path), str(json_path)]

    decision = state.get("human_review")
    human_approved = (
        state.get("require_human_approval", False)
        and decision is not None
        and decision.action in ("approve", "filter")
    )
    posted, note = False, "Review saved locally; not posted to GitHub."
    if ctx.get("post_to_github") and ctx.get("pr_number"):
        if not human_approved:
            note = "Not posted: posting needs require_human_approval=True and a human approval."
        else:
            try:
                comment_url = _gh(
                    "pr", "comment", str(ctx["pr_number"]), "--repo", ctx["repo"], "--body-file", str(md_path)
                ).strip()
                artifacts.append(comment_url)
                posted, note = True, f"Posted review comment: {comment_url}"
            except RuntimeError as e:  # fail loud, keep the local report
                return {
                    "deliverable_result": DeliverableResult(
                        module_name="pr_review", status="failed", summary=f"Saved locally; posting failed: {e}",
                        artifacts=artifacts,
                    )
                }

    return {
        "deliverable_result": DeliverableResult(
            module_name="pr_review",
            status="completed",
            summary=f"{report.overall_verdict}: {len(report.findings)} finding(s) for {report.target_id}. {note}",
            artifacts=artifacts,
            metadata={"posted_to_github": posted, "severity_counts": report.severity_counts()},
        )
    }


def build_github_pr_review_module() -> ContextModule:
    return ContextModule(
        name="pr_review",
        description="GitHub PR (or raw diff) -> markdown/JSON review, optional human-approved PR comment",
        inbound_node=github_pr_inbound,
        outbound_node=github_pr_outbound,
    )
