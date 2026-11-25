"""Stub Outbound Adapter nodes/subgraphs for all three planned I/O modules.

Key architectural highlight:
- Modules 1 (PR comments) and 3 (PDF report) are implemented as simple LangGraph nodes.
- Module 2 (Existing Codebase -> Remediation PR) is implemented as a **compiled LangGraph
  agentic subgraph** (`plan_remediations -> apply_code_edits -> open_remediation_pr`)
  to demonstrate how an outbound adapter can run its own private multi-step tool loop
  (file edits, git commits, `gh pr create`) while plugging into the exact same pipeline slot.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from typing_extensions import TypedDict

from ...domain.models import DeliverableResult, FindingsReport, PullRequestDiffLocation
from ...domain.state import PipelineState
from ...ports.adapters import ContextModule
from ..inbound.stubs import (
    codebase_review_inbound_stub,
    pr_review_inbound_stub,
    product_spec_inbound_stub,
)


# ---------------------------------------------------------------------------
# Module 1 Outbound Stub: PR Review Comments (future: `gh pr review` / `gh api`)
# ---------------------------------------------------------------------------


def pr_review_outbound_stub(state: PipelineState) -> dict[str, Any]:
    """Format `FindingsReport` into anchored GitHub PR inline comments (stub)."""
    report = state["findings_report"]
    ctx = state.get("adapter_context", {})
    repo = ctx.get("repo", "unknown/repo")
    pr_number = ctx.get("pr_number", 0)

    drafted_comments: list[dict[str, Any]] = []
    for finding in report.findings:
        file_path = (
            finding.location.file_path
            if isinstance(finding.location, PullRequestDiffLocation)
            else "GENERAL"
        )
        line = (
            finding.location.end_line
            if isinstance(finding.location, PullRequestDiffLocation)
            else None
        )
        fca_refs = ", ".join(c.reference for c in finding.fca_citations) or "N/A"
        cca_refs = ", ".join(c.reference for c in finding.cca_citations) or "N/A"
        drafted_comments.append(
            {
                "finding_id": finding.finding_id,
                "path": file_path,
                "line": line,
                "body": (
                    f"**[{finding.severity.upper()}] {finding.title}**\n\n"
                    f"- **FCA Handbook:** {fca_refs}\n"
                    f"- **Consumer Credit Act:** {cca_refs}\n"
                    f"- **Remediation:** {finding.remediation_guidance}"
                ),
            }
        )

    result = DeliverableResult(
        module_name="pr_review",
        status="completed",
        summary=(
            f"[STUB gh pr review] Prepared {len(drafted_comments)} inline comment(s) "
            f"for {repo}#{pr_number} (verdict={report.overall_verdict})."
        ),
        artifacts=[f"{ctx.get('pr_url', f'https://github.com/{repo}/pull/{pr_number}')}#review-stub"],
        metadata={"pr_comments": drafted_comments, "head_sha": ctx.get("head_sha")},
    )
    return {"deliverable_result": result}


# ---------------------------------------------------------------------------
# Module 2 Outbound Stub: Agentic Remediation Subgraph (future: edits + git + `gh pr create`)
# ---------------------------------------------------------------------------


class _CodebaseRemediationState(TypedDict, total=False):
    """Private state for the Codebase Remediation Outbound Subgraph.

    Shares `findings_report`, `adapter_context` (in) and `deliverable_result` (out)
    with `PipelineState`, while keeping `planned_edits` and `applied_files` private.
    """

    findings_report: FindingsReport
    adapter_context: dict[str, Any]
    planned_edits: list[dict[str, Any]]
    applied_files: list[str]
    deliverable_result: DeliverableResult


def build_codebase_remediation_outbound_subgraph() -> CompiledStateGraph:
    """Build a multi-step LangGraph subgraph representing an agentic remediation adapter.

    In production, `apply_code_edits` will bind file-edit, test-runner, git, and `gh` tools
    in an agentic tool loop to produce verified commits and open a real Pull Request.
    """

    def plan_remediations(state: _CodebaseRemediationState) -> dict[str, Any]:
        report = state["findings_report"]
        edits = [
            {
                "finding_id": f.finding_id,
                "file_path": getattr(f.location, "file_path", "UNKNOWN"),
                "guidance": f.remediation_guidance,
                "suggested_change": f.suggested_change,
            }
            for f in report.findings
        ]
        return {"planned_edits": edits}

    def apply_code_edits(state: _CodebaseRemediationState) -> dict[str, Any]:
        # Future agentic tool calls: read_file -> edit_file -> run_tests -> git_commit
        edits = state.get("planned_edits", [])
        applied = list(dict.fromkeys(e["file_path"] for e in edits if e["file_path"] != "UNKNOWN"))
        return {"applied_files": applied}

    def open_remediation_pr(state: _CodebaseRemediationState) -> dict[str, Any]:
        report = state["findings_report"]
        ctx = state.get("adapter_context", {})
        applied = state.get("applied_files", [])
        repo_url = ctx.get("repo_url", "https://github.com/unknown/repo")
        branch = f"compliance/fca-cca-{report.report_id.lower()}"

        if not applied:
            return {
                "deliverable_result": DeliverableResult(
                    module_name="codebase_review",
                    status="skipped",
                    summary="No remediable findings; skipped opening a remediation PR.",
                )
            }

        pr_url = f"{repo_url}/pull/999-stub"
        return {
            "deliverable_result": DeliverableResult(
                module_name="codebase_review",
                status="completed",
                summary=(
                    f"[STUB agentic remediation PR] Edited {len(applied)} file(s) on branch "
                    f"'{branch}' in {ctx.get('workspace_path')} and opened {pr_url}."
                ),
                artifacts=[pr_url],
                metadata={
                    "branch": branch,
                    "workspace_path": ctx.get("workspace_path"),
                    "edited_files": applied,
                    "planned_edits": state.get("planned_edits", []),
                },
            )
        }

    builder = StateGraph(_CodebaseRemediationState)
    builder.add_node("plan_remediations", plan_remediations)
    builder.add_node("apply_code_edits", apply_code_edits)
    builder.add_node("open_remediation_pr", open_remediation_pr)

    builder.add_edge(START, "plan_remediations")
    builder.add_edge("plan_remediations", "apply_code_edits")
    builder.add_edge("apply_code_edits", "open_remediation_pr")
    builder.add_edge("open_remediation_pr", END)
    return builder.compile()


# ---------------------------------------------------------------------------
# Module 3 Outbound Stub: Product Spec PDF Report Generator
# ---------------------------------------------------------------------------


def product_spec_outbound_stub(state: PipelineState) -> dict[str, Any]:
    """Render `FindingsReport` into a PDF findings report descriptor (stub)."""
    report = state["findings_report"]
    ctx = state.get("adapter_context", {})
    output_path = str(ctx.get("output_pdf_path", f"reports/{report.report_id}.pdf"))

    result = DeliverableResult(
        module_name="product_spec_review",
        status="completed",
        summary=(
            f"[STUB PDF generator] Rendered {len(report.findings)} finding(s) "
            f"(verdict={report.overall_verdict}) to '{output_path}'."
        ),
        artifacts=[output_path],
        metadata={
            "input_pdf_path": ctx.get("input_pdf_path"),
            "output_pdf_path": output_path,
            "severity_counts": report.severity_counts(),
        },
    )
    return {"deliverable_result": result}


# ---------------------------------------------------------------------------
# Bundled ContextModule Factories
# ---------------------------------------------------------------------------


def build_pr_review_module() -> ContextModule:
    return ContextModule(
        name="pr_review",
        description="PR URL via gh CLI -> inline PR review comments via gh CLI",
        inbound_node=pr_review_inbound_stub,
        outbound_node=pr_review_outbound_stub,
    )


def build_codebase_review_module() -> ContextModule:
    return ContextModule(
        name="codebase_review",
        description="GitHub repo default branch -> agentic remediation subgraph opening a PR",
        inbound_node=codebase_review_inbound_stub,
        outbound_node=build_codebase_remediation_outbound_subgraph(),
    )


def build_product_spec_module() -> ContextModule:
    return ContextModule(
        name="product_spec_review",
        description="Incoming Product Spec PDF -> structured PDF compliance findings report",
        inbound_node=product_spec_inbound_stub,
        outbound_node=product_spec_outbound_stub,
    )
