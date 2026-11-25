"""FastAPI HTTP adapter exposing the real FCA / CCA Compliance Agent to web UIs.

Provides:
  - `POST /review` and `POST /review/invoke` (ReguLens direct & LangServe contracts)
  - `GET /api/targets` and `GET /api/targets/{owner}/{repo}/pulls` (discoverable GitHub repos & PRs)
  - `GET /api/findings` and `GET /api/reports/{report_id}` (persisted & live compliance findings)
  - `POST /api/runs`, `GET /api/runs`, `GET /api/runs/{run_id}` (async repo / PR / spec / chat runs)
  - `GET /health` (readiness & MCP connectivity summary)

Follows Hexagonal Architecture (`adapters/ -> core/ -> ports/ -> domain/`):
always wires real integrations (FCA Handbook RAG MCP, CCA 1974 MCP, real LLM, real GitHub CLI/API).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

from ..agent import build_default_core_subgraph
from ..core import build_pipeline_for_module
from ..domain.models import (
    AnalysisInput,
    AnalysisUnit,
    CodeLocation,
    Finding,
    FindingsReport,
    PullRequestDiffLocation,
)
from ..mcp_server.bootstrap import (
    DEFAULT_CCA_MCP_PORT,
    DEFAULT_FCA_MCP_URL,
    DEFAULT_MCP_HOST,
    is_port_listening,
)
from .chat import build_combined_graph
from .github_codebase import (
    REPORTS_DIR,
    build_github_codebase_review_module,
    render_codebase_markdown,
)
from .github_pr import build_github_pr_review_module
from .llm.langchain_llm import load_repo_env
from .outbound.github_client import GitHubCLIAdapter
from .product_spec import build_product_spec_review_module

DEFAULT_API_HOST = "127.0.0.1"
DEFAULT_API_PORT = 8002
DEFAULT_GITHUB_ORG = "natchainsix"

_REPORT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_SLUG_PART_RE = re.compile(r"^[\w.-]+$")

_STATUS_TO_REGULENS: dict[str, str] = {
    "non_compliant": "NON_COMPLIANT",
    "potential_breach": "NON_COMPLIANT",
    "needs_review": "NEEDS_CONTEXT",
    "observation": "COMPLIANT_WITHIN_SCOPE",
}
_SEVERITY_TO_REGULENS: dict[str, str] = {
    "critical": "CRITICAL",
    "high": "HIGH",
    "medium": "MEDIUM",
    "low": "LOW",
    "info": "LOW",
}


# ---------------------------------------------------------------------------
# Request / Response Schemas
# ---------------------------------------------------------------------------


class ReviewFile(BaseModel):
    path: str
    content: str


class ReviewRequestPayload(BaseModel):
    repository: str
    commit: str = "HEAD"
    files: list[ReviewFile] = Field(default_factory=list)
    instructions: str = ""
    specialty: str = "fca_compliance"


class LangServeReviewEnvelope(BaseModel):
    input: ReviewRequestPayload


class RunRequestPayload(BaseModel):
    mode: Literal["codebase_review", "pr_review", "product_spec", "chat"] = "codebase_review"
    repo_url: str | None = None
    repo: str | None = None
    branch: str = "main"
    pr_url: str | None = None
    pr_number: int | None = None
    create_pr: bool = False
    draft_pr: bool = False
    post_to_github: bool = False
    output_type: Literal["report", "pr_comment", "remediation_pr", "pdf_report"] | None = None
    spec_text: str | None = None
    spec_path: str | None = None
    message: str | None = None


# ---------------------------------------------------------------------------
# Mapping Helpers (Domain FindingsReport <-> ReguLens UI Contract)
# ---------------------------------------------------------------------------


def _classify_file_type(path: str) -> Literal["code", "config", "spec_section", "other"]:
    suffix = Path(path).suffix.lower()
    if suffix in {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".cs", ".rb", ".rs", ".sql", ".sh"}:
        return "code"
    if suffix in {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml"}:
        return "config"
    if suffix in {".md", ".txt", ".pdf"}:
        return "spec_section"
    return "other"


def _clamp_str(value: str, fallback: str, limit: int = 3900) -> str:
    cleaned = value.strip()
    if not cleaned:
        cleaned = fallback
    return cleaned[:limit]


def _resolve_finding_line(finding: Finding, file_lines: list[str]) -> int:
    """Resolve a valid 1-based line number matching evidence where possible."""
    max_line = max(1, len(file_lines))
    excerpt = (finding.evidence_excerpt or "").strip()
    if excerpt:
        first_line = next((ln.strip() for ln in excerpt.splitlines() if ln.strip()), "")
        if first_line:
            for idx, src_line in enumerate(file_lines, start=1):
                if first_line in src_line:
                    return idx
            lower_first = first_line.lower()
            for idx, src_line in enumerate(file_lines, start=1):
                if lower_first in src_line.lower():
                    return idx

    loc = finding.location
    raw_line: int | None = None
    if isinstance(loc, (CodeLocation, PullRequestDiffLocation)):
        raw_line = loc.start_line
    if isinstance(raw_line, int) and 1 <= raw_line <= max_line:
        return raw_line
    return 1


def _finding_to_regulens(
    finding: Finding,
    *,
    files_by_path: dict[str, list[str]],
    unit_path_by_id: dict[str, str],
) -> dict[str, Any] | None:
    """Convert a domain `Finding` into the strict schema expected by `natchainsixagent-ui`."""
    loc = finding.location
    candidate_path = getattr(loc, "file_path", None) or unit_path_by_id.get(finding.unit_id)
    if not candidate_path or candidate_path not in files_by_path:
        if len(files_by_path) == 1:
            candidate_path = next(iter(files_by_path))
        else:
            return None

    file_lines = files_by_path[candidate_path]
    if not file_lines:
        return None

    line_start = _resolve_finding_line(finding, file_lines)
    refs = list(
        dict.fromkeys(
            c.reference.strip()
            for c in (*finding.fca_citations, *finding.cca_citations)
            if c.reference and c.reference.strip()
        )
    )
    regulation = "; ".join(refs) if refs else "FCA Handbook / Consumer Credit Act 1974"

    line_text = file_lines[line_start - 1].strip() if 1 <= line_start <= len(file_lines) else ""
    evidence = _clamp_str(finding.evidence_excerpt, line_text or f"{candidate_path}:{line_start}")

    remediation_parts = [finding.remediation_guidance.strip()]
    if finding.fca_vs_cca_comparison.strip():
        remediation_parts.append(f"FCA vs CCA: {finding.fca_vs_cca_comparison.strip()}")
    remediation = _clamp_str("\n".join(p for p in remediation_parts if p), finding.description)

    return {
        "status": _STATUS_TO_REGULENS.get(finding.status, "NON_COMPLIANT"),
        "severity": _SEVERITY_TO_REGULENS.get(finding.severity, "MEDIUM"),
        "title": _clamp_str(finding.title, "Compliance finding"),
        "file": candidate_path,
        "line_start": line_start,
        "regulation": _clamp_str(regulation, "FCA Handbook / Consumer Credit Act 1974"),
        "evidence": evidence,
        "remediation": remediation,
    }


def _persist_report(report: FindingsReport, reports_dir: Path) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / f"{report.report_id}.json"
    md_path = reports_dir / f"{report.report_id}.md"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(render_codebase_markdown(report), encoding="utf-8")
    return md_path, json_path


def _load_persisted_reports(reports_dir: Path) -> list[FindingsReport]:
    if not reports_dir.is_dir():
        return []
    reports: list[FindingsReport] = []
    for path in reports_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or "report_id" not in payload or "findings" not in payload:
                continue
            reports.append(FindingsReport.model_validate(payload))
        except Exception:
            continue
    reports.sort(key=lambda r: r.generated_at, reverse=True)
    return reports


def _probe_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlsplit(url)
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return is_port_listening(host, port, timeout=0.35)


# ---------------------------------------------------------------------------
# FastAPI Application Factory
# ---------------------------------------------------------------------------


def create_app(
    *,
    core_subgraph: Any | None = None,
    core_factory: Callable[[], Any] | None = None,
    github_client: Any | None = None,
    reports_dir: Path | None = None,
    default_org: str | None = None,
    pipelines: dict[str, Any] | None = None,
) -> FastAPI:
    """Create the FastAPI application wired to real compliance graphs and GitHub discovery."""
    load_repo_env()

    resolved_reports_dir = Path(reports_dir) if reports_dir is not None else REPORTS_DIR
    resolved_org = (default_org or os.environ.get("GITHUB_ORG") or DEFAULT_GITHUB_ORG).strip()
    gh_client = github_client or GitHubCLIAdapter()

    state_cache: dict[str, Any] = {
        "core": core_subgraph,
        "pipelines": dict(pipelines) if pipelines else None,
        "runs": {},
        "tasks": set(),
    }

    def get_core() -> Any:
        if state_cache["core"] is None:
            factory = core_factory or build_default_core_subgraph
            state_cache["core"] = factory()
        return state_cache["core"]

    def get_pipelines() -> dict[str, Any]:
        if state_cache["pipelines"] is None:
            core = get_core()
            pr_pipe = build_pipeline_for_module(
                build_github_pr_review_module(github_client=gh_client, reports_dir=resolved_reports_dir),
                core_subgraph=core,
            )
            codebase_pipe = build_pipeline_for_module(
                build_github_codebase_review_module(github_client=gh_client, reports_dir=resolved_reports_dir),
                core_subgraph=core,
            )
            spec_pipe = build_pipeline_for_module(
                build_product_spec_review_module(reports_dir=resolved_reports_dir),
                core_subgraph=core,
            )
            combined_pipe = build_combined_graph(
                pr_review=pr_pipe,
                codebase_review=codebase_pipe,
                product_spec=spec_pipe,
            )
            state_cache["pipelines"] = {
                "pr_review": pr_pipe,
                "codebase_review": codebase_pipe,
                "product_spec": spec_pipe,
                "chat": combined_pipe,
            }
        return state_cache["pipelines"]

    app = FastAPI(
        title="FCA & CCA Compliance Agent API",
        version="0.1.0",
        description="HTTP adapter for the Hexagonal FCA Handbook & Consumer Credit Act Compliance Agent.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:8000",
            "http://localhost:8000",
            "http://127.0.0.1:3000",
            "http://localhost:3000",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Health & Status
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health() -> dict[str, Any]:
        fca_url = os.environ.get("FCA_MCP_URL") or DEFAULT_FCA_MCP_URL
        cca_url = os.environ.get("CCA_MCP_URL") or f"http://{DEFAULT_MCP_HOST}:{DEFAULT_CCA_MCP_PORT}/mcp"
        reports = _load_persisted_reports(resolved_reports_dir)
        return {
            "status": "ok",
            "default_org": resolved_org,
            "model_name": os.environ.get("MODEL_NAME", "openai:gpt-6-luna"),
            "fca_mcp_url": fca_url,
            "fca_mcp_reachable": _probe_url(fca_url),
            "cca_mcp_url": cca_url,
            "cca_mcp_reachable": _probe_url(cca_url),
            "reports_count": len(reports),
            "active_runs": sum(1 for r in state_cache["runs"].values() if r["status"] in {"queued", "running"}),
        }

    # ------------------------------------------------------------------
    # Direct & LangServe File Review Endpoints (for natchainsixagent-ui)
    # ------------------------------------------------------------------

    async def _execute_file_review(req: ReviewRequestPayload) -> dict[str, Any]:
        files_by_path: dict[str, list[str]] = {}
        units: list[AnalysisUnit] = []
        unit_path_by_id: dict[str, str] = {}

        for idx, file_obj in enumerate(req.files, start=1):
            lines = file_obj.content.splitlines()
            if not lines:
                continue
            files_by_path[file_obj.path] = lines
            unit_id = f"file-{idx}"
            unit_path_by_id[unit_id] = file_obj.path
            units.append(
                AnalysisUnit(
                    unit_id=unit_id,
                    title=file_obj.path,
                    content=file_obj.content,
                    content_type=_classify_file_type(file_obj.path),
                    location=CodeLocation(
                        file_path=file_obj.path,
                        start_line=1,
                        end_line=len(lines),
                    ),
                    metadata={"commit": req.commit, "specialty": req.specialty},
                )
            )

        analysis_input = AnalysisInput(
            target_id=req.repository,
            source_type="codebase",
            title=f"Compliance review: {req.repository} ({req.commit[:12]})",
            summary=req.instructions[:1000] if req.instructions else f"Review of {req.repository}",
            units=units,
        )

        started = time.monotonic()
        logger.info(
            "POST /review started for %s (%d file(s), commit=%s)",
            req.repository,
            len(units),
            req.commit[:12],
        )
        core = get_core()
        result = await core.ainvoke({"analysis_input": analysis_input})
        report: FindingsReport = result["findings_report"]
        _persist_report(report, resolved_reports_dir)

        mapped: list[dict[str, Any]] = []
        for finding in report.findings:
            converted = _finding_to_regulens(
                finding,
                files_by_path=files_by_path,
                unit_path_by_id=unit_path_by_id,
            )
            if converted is not None:
                mapped.append(converted)

        logger.info(
            "POST /review completed for %s in %.1fs: report=%s verdict=%s findings=%d",
            req.repository,
            time.monotonic() - started,
            report.report_id,
            report.overall_verdict,
            len(mapped),
        )
        return {
            "findings": mapped[:200],
            "report_id": report.report_id,
            "overall_verdict": report.overall_verdict,
            "executive_summary": report.executive_summary,
        }

    @app.post("/review")
    async def review_direct(req: ReviewRequestPayload) -> dict[str, Any]:
        return await _execute_file_review(req)

    @app.post("/review/invoke")
    async def review_langserve(envelope: LangServeReviewEnvelope) -> dict[str, Any]:
        return {"output": await _execute_file_review(envelope.input)}

    # ------------------------------------------------------------------
    # Target Discovery (Repositories & Pull Requests)
    # ------------------------------------------------------------------

    @app.get("/api/targets")
    async def list_targets(
        org: str | None = Query(default=None, description="GitHub organisation or user to enumerate."),
        include_prs: bool = Query(default=True, description="Include pull requests for each repository."),
        pr_state: Literal["open", "closed", "all"] = Query(default="open"),
    ) -> dict[str, Any]:
        target_org = (org or resolved_org).strip()
        warnings: list[str] = []
        repos: list[dict[str, Any]] = []

        if target_org:
            try:
                repos = await asyncio.to_thread(gh_client.list_org_repos, target_org)
            except Exception as exc:
                warnings.append(f"Could not list repositories for {target_org}: {exc}")

        extra_repos_env = os.environ.get("COMPLIANCE_REPOS", "").strip()
        known_slugs = {r["full_name"] for r in repos if r.get("full_name")}
        if extra_repos_env:
            for slug in (s.strip() for s in extra_repos_env.split(",") if s.strip()):
                if "/" in slug and slug not in known_slugs:
                    repos.append(
                        {
                            "full_name": slug,
                            "url": f"https://github.com/{slug}",
                            "default_branch": "main",
                            "description": "Configured via COMPLIANCE_REPOS",
                            "private": False,
                            "pushed_at": None,
                        }
                    )
                    known_slugs.add(slug)

        enriched: list[dict[str, Any]] = []
        for repo in repos:
            entry = dict(repo)
            slug = entry.get("full_name", "")
            prs: list[dict[str, Any]] = []
            if include_prs and slug:
                try:
                    prs = await asyncio.to_thread(gh_client.list_repo_prs, slug, state=pr_state)
                except Exception as exc:
                    warnings.append(f"Could not list PRs for {slug}: {exc}")
            entry["pull_requests"] = prs
            enriched.append(entry)

        return {
            "org": target_org,
            "repositories": enriched,
            "warnings": warnings,
            "capabilities": ["codebase_review", "pr_review", "product_spec", "chat"],
        }

    @app.get("/api/targets/{owner}/{repo}/pulls")
    async def list_repo_pulls(
        owner: str,
        repo: str,
        state: Literal["open", "closed", "all"] = Query(default="open"),
    ) -> dict[str, Any]:
        if not _SLUG_PART_RE.match(owner) or not _SLUG_PART_RE.match(repo):
            raise HTTPException(status_code=400, detail="Invalid repository owner or name.")
        slug = f"{owner}/{repo}"
        try:
            prs = await asyncio.to_thread(gh_client.list_repo_prs, slug, state=state)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Failed to list PRs for {slug}: {exc}") from exc
        return {"repository": slug, "state": state, "pull_requests": prs}

    # ------------------------------------------------------------------
    # Persisted Findings & Reports
    # ------------------------------------------------------------------

    @app.get("/api/findings")
    async def list_findings(
        target_id: str | None = Query(default=None),
        source_type: str | None = Query(default=None),
        severity: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> dict[str, Any]:
        reports = _load_persisted_reports(resolved_reports_dir)
        if target_id:
            needle = target_id.strip().lower()
            reports = [r for r in reports if needle in r.target_id.lower()]
        if source_type:
            reports = [r for r in reports if r.source_type == source_type]

        selected_reports = reports[:limit]
        report_summaries: list[dict[str, Any]] = []
        flattened_findings: list[dict[str, Any]] = []

        sev_filter = severity.strip().lower() if severity else None
        for report in selected_reports:
            report_summaries.append(
                {
                    "report_id": report.report_id,
                    "target_id": report.target_id,
                    "source_type": report.source_type,
                    "title": report.title,
                    "generated_at": report.generated_at.isoformat(),
                    "overall_verdict": report.overall_verdict,
                    "executive_summary": report.executive_summary,
                    "units_total": report.units_total,
                    "units_triaged_in": report.units_triaged_in,
                    "units_skipped": report.units_skipped,
                    "severity_counts": report.severity_counts(),
                    "findings_count": len(report.findings),
                }
            )
            for finding in report.findings:
                if sev_filter and finding.severity.lower() != sev_filter:
                    continue
                dumped = finding.model_dump(mode="json")
                dumped["report_id"] = report.report_id
                dumped["target_id"] = report.target_id
                dumped["source_type"] = report.source_type
                dumped["generated_at"] = report.generated_at.isoformat()
                flattened_findings.append(dumped)

        return {
            "total_reports": len(reports),
            "total_findings": len(flattened_findings),
            "reports": report_summaries,
            "findings": flattened_findings,
        }

    @app.get("/api/reports/{report_id}")
    async def get_report(report_id: str) -> dict[str, Any]:
        if not _REPORT_ID_RE.match(report_id):
            raise HTTPException(status_code=400, detail="Invalid report ID.")
        json_path = resolved_reports_dir / f"{report_id}.json"
        if not json_path.is_file():
            raise HTTPException(status_code=404, detail=f"Report {report_id} not found.")
        report = FindingsReport.model_validate_json(json_path.read_text(encoding="utf-8"))
        md_path = resolved_reports_dir / f"{report_id}.md"
        markdown = md_path.read_text(encoding="utf-8") if md_path.is_file() else render_codebase_markdown(report)
        return {
            "report": report.model_dump(mode="json"),
            "markdown": markdown,
        }

    # ------------------------------------------------------------------
    # Async Full Pipeline Runs (Repo / PR / Spec / Chat)
    # ------------------------------------------------------------------

    def _build_run_input(req: RunRequestPayload) -> tuple[str, dict[str, Any]]:
        if req.mode == "codebase_review":
            if not req.repo_url and not req.repo:
                raise HTTPException(status_code=400, detail="codebase_review requires repo_url or repo.")
            output_type = req.output_type or ("remediation_pr" if req.create_pr else "report")
            raw: dict[str, Any] = {
                "default_branch": req.branch or "main",
                "create_pr": req.create_pr,
                "draft_pr": req.draft_pr,
                "output_type": output_type,
            }
            if req.repo_url:
                raw["repo_url"] = req.repo_url
                target = req.repo_url
            else:
                raw["repo"] = req.repo
                target = str(req.repo)
            return target, {"raw_input": raw, "output_type": output_type}

        if req.mode == "pr_review":
            if not req.pr_url and not (req.repo and req.pr_number is not None):
                raise HTTPException(status_code=400, detail="pr_review requires pr_url or repo + pr_number.")
            output_type = req.output_type or ("pr_comment" if req.post_to_github else "report")
            raw = {"post_to_github": req.post_to_github, "output_type": output_type}
            if req.pr_url:
                raw["pr_url"] = req.pr_url
                target = req.pr_url
            else:
                raw["repo"] = req.repo
                raw["pr_number"] = req.pr_number
                target = f"{req.repo}#{req.pr_number}"
            return target, {"raw_input": raw, "output_type": output_type}

        if req.mode == "product_spec":
            if not req.spec_text and not req.spec_path:
                raise HTTPException(status_code=400, detail="product_spec requires spec_text or spec_path.")
            output_type = req.output_type or "pdf_report"
            raw = {"output_type": output_type}
            if req.spec_text:
                raw["spec_text"] = req.spec_text
                target = "inline-product-spec"
            else:
                raw["spec_path"] = req.spec_path
                target = str(req.spec_path)
            return target, {"raw_input": raw, "output_type": output_type}

        if not req.message or not req.message.strip():
            raise HTTPException(status_code=400, detail="chat mode requires a non-empty message.")
        from langchain_core.messages import HumanMessage

        return req.message.strip()[:120], {"messages": [HumanMessage(content=req.message.strip())]}

    async def _run_pipeline_job(run_id: str, mode: str, payload: dict[str, Any]) -> None:
        record = state_cache["runs"][run_id]
        record["status"] = "running"
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        logger.info(
            "Run %s started: mode=%s target=%s output_type=%s",
            run_id,
            mode,
            record["target"],
            payload.get("output_type") or (payload.get("raw_input") or {}).get("output_type"),
        )
        try:
            pipeline = get_pipelines()[mode]
            result = await pipeline.ainvoke(payload)
            report = result.get("findings_report")
            deliverable = result.get("deliverable_result")
            messages = result.get("messages") or []
            reply_text = None
            if messages:
                last_msg = messages[-1]
                reply_text = getattr(last_msg, "content", None)

            record["status"] = "completed"
            record["output_type"] = result.get("output_type")
            record["findings_report"] = report.model_dump(mode="json") if report is not None else None
            record["deliverable_result"] = deliverable.model_dump(mode="json") if deliverable is not None else None
            record["reply"] = reply_text
            logger.info(
                "Run %s completed in %.1fs: report=%s verdict=%s findings=%s deliverable=%s",
                run_id,
                time.monotonic() - started,
                report.report_id if report is not None else None,
                report.overall_verdict if report is not None else None,
                len(report.findings) if report is not None else 0,
                deliverable.summary if deliverable is not None else "none",
            )
        except Exception as exc:
            record["status"] = "failed"
            record["error"] = f"{type(exc).__name__}: {exc}"
            logger.exception("Run %s failed after %.1fs: %s", run_id, time.monotonic() - started, exc)
        finally:
            record["updated_at"] = datetime.now(timezone.utc).isoformat()

    @app.post("/api/runs", status_code=202)
    async def create_run(req: RunRequestPayload) -> dict[str, Any]:
        target, payload = _build_run_input(req)
        run_id = f"RUN-{uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()
        record: dict[str, Any] = {
            "run_id": run_id,
            "mode": req.mode,
            "target": target,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "output_type": None,
            "findings_report": None,
            "deliverable_result": None,
            "reply": None,
            "error": None,
        }
        state_cache["runs"][run_id] = record
        task = asyncio.create_task(_run_pipeline_job(run_id, req.mode, payload))
        state_cache["tasks"].add(task)
        task.add_done_callback(state_cache["tasks"].discard)
        return record

    @app.get("/api/runs")
    async def list_runs(limit: int = Query(default=30, ge=1, le=200)) -> dict[str, Any]:
        runs = sorted(
            state_cache["runs"].values(),
            key=lambda r: r["created_at"],
            reverse=True,
        )[:limit]
        return {"runs": runs}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        record = state_cache["runs"].get(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found.")
        return record

    return app


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    parser = argparse.ArgumentParser(
        description="Run the FCA & CCA Compliance Agent HTTP API server."
    )
    parser.add_argument("--host", default=DEFAULT_API_HOST, help=f"Bind host (default: {DEFAULT_API_HOST}).")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_API_PORT,
        help=f"Bind port (default: {DEFAULT_API_PORT}).",
    )
    parser.add_argument(
        "--github-org",
        default=None,
        help=f"Default GitHub organisation for /api/targets (default: GITHUB_ORG or {DEFAULT_GITHUB_ORG}).",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
        force=True,
    )
    app = create_app(default_org=args.github_org)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
