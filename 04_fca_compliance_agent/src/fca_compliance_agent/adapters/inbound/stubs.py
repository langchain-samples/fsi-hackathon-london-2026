"""Stub Inbound Adapter nodes demonstrating all three planned I/O modules.

Each stub transforms `state["raw_input"]` into:
  - `analysis_input`: normalised `AnalysisInput` consumed by the Core subgraph
  - `adapter_context`: opaque metadata passed straight through to the paired Outbound Adapter
"""

from __future__ import annotations

from typing import Any

from ...domain.models import (
    AnalysisInput,
    AnalysisUnit,
    CodeLocation,
    DocumentLocation,
    PullRequestDiffLocation,
)
from ...domain.state import PipelineState


def pr_review_inbound_stub(state: PipelineState) -> dict[str, Any]:
    """Module 1 Inbound Stub: PR link -> normalised diff AnalysisUnits (future: `gh pr diff`)."""
    raw = state.get("raw_input", {})
    pr_url = str(raw.get("pr_url", "https://github.com/acme-bank/credit-engine/pull/42"))
    pr_number = int(raw.get("pr_number", 42))
    repo = str(raw.get("repo", "acme-bank/credit-engine"))

    custom_units = raw.get("units")
    if custom_units:
        units = [
            u if isinstance(u, AnalysisUnit) else AnalysisUnit.model_validate(u)
            for u in custom_units
        ]
    else:
        units = [
            AnalysisUnit(
                unit_id="pr-diff-1",
                title="src/underwriting/approve.py",
                content_type="diff",
                location=PullRequestDiffLocation(
                    file_path="src/underwriting/approve.py",
                    start_line=18,
                    end_line=32,
                    hunk_header="@@ -18,6 +18,14 @@ def evaluate_loan_application(app):",
                    diff_position=7,
                ),
                content=(
                    "+# Fast-track promotional campaign\n"
                    "+skip_affordability = True\n"
                    "+BANNER_COPY = 'Instant cash - guaranteed approval in 60 seconds!'\n"
                ),
            ),
            AnalysisUnit(
                unit_id="pr-diff-2",
                title="src/contracts/withdrawal.py",
                content_type="diff",
                location=PullRequestDiffLocation(
                    file_path="src/contracts/withdrawal.py",
                    start_line=5,
                    end_line=12,
                    hunk_header="@@ -5,4 +5,8 @@",
                    diff_position=3,
                ),
                content="+withdrawal_days = 7  # shortened cooling-off window\n",
            ),
            AnalysisUnit(
                unit_id="pr-diff-3",
                title="static/styles/button.css",
                content_type="other",
                location=PullRequestDiffLocation(
                    file_path="static/styles/button.css",
                    start_line=1,
                    end_line=4,
                    hunk_header="@@ -1,2 +1,4 @@",
                    diff_position=1,
                ),
                content="+.btn-primary { border-radius: 6px; }\n",
            ),
        ]

    analysis_input = AnalysisInput(
        target_id=f"{repo}#{pr_number}",
        source_type="pr_review",
        title=str(raw.get("title", f"PR #{pr_number}: Fast-track personal loan onboarding")),
        summary=str(raw.get("summary", "Adds fast-track personal loan approval and updates withdrawal window.")),
        units=units,
    )
    return {
        "analysis_input": analysis_input,
        "adapter_context": {
            "repo": repo,
            "pr_number": pr_number,
            "pr_url": pr_url,
            "head_sha": raw.get("head_sha", "abc123def456"),
        },
    }


def codebase_review_inbound_stub(state: PipelineState) -> dict[str, Any]:
    """Module 2 Inbound Stub: GitHub repo URL -> codebase AnalysisUnits (future: `git clone`)."""
    raw = state.get("raw_input", {})
    repo_url = str(raw.get("repo_url", "https://github.com/acme-bank/retail-lending"))
    default_branch = str(raw.get("default_branch", "main"))

    units = [
        AnalysisUnit(
            unit_id="code-1",
            title="lending/collections/default_notice.py",
            content_type="code",
            location=CodeLocation(
                file_path="lending/collections/default_notice.py",
                start_line=10,
                end_line=28,
                symbol_name="issue_default_notice",
            ),
            content=(
                "DEFAULT_NOTICE_DAYS = 5\n"
                "def issue_default_notice(account_id: str) -> dict:\n"
                "    # default_notice_days = 5 -> immediate termination after 5 days\n"
                "    return {'account_id': account_id, 'cure_days': DEFAULT_NOTICE_DAYS}\n"
            ),
        ),
        AnalysisUnit(
            unit_id="code-2",
            title="lending/marketing/banner.py",
            content_type="code",
            location=CodeLocation(
                file_path="lending/marketing/banner.py",
                start_line=1,
                end_line=12,
                symbol_name="render_rate_banner",
            ),
            content=(
                "def render_rate_banner():\n"
                "    hide_apr = True  # apr_footer_only, show headline rate only\n"
                "    return {'headline': '3.9% interest', 'hide_apr': hide_apr}\n"
            ),
        ),
        AnalysisUnit(
            unit_id="code-3",
            title="assets/theme.css",
            content_type="other",
            location=CodeLocation(
                file_path="assets/theme.css",
                start_line=1,
                end_line=5,
            ),
            content="body { font-family: Inter, sans-serif; }\n",
        ),
    ]

    analysis_input = AnalysisInput(
        target_id=repo_url,
        source_type="codebase",
        title=f"Default-branch compliance scan: {repo_url} ({default_branch})",
        summary="Full default-branch review of UK consumer lending platform.",
        units=units,
    )
    return {
        "analysis_input": analysis_input,
        "adapter_context": {
            "repo_url": repo_url,
            "default_branch": default_branch,
            "workspace_path": raw.get("workspace_path", "/tmp/stub-workspaces/retail-lending"),
            "base_sha": raw.get("base_sha", "999888777aaa"),
        },
    }


def product_spec_inbound_stub(state: PipelineState) -> dict[str, Any]:
    """Module 3 Inbound Stub: Incoming PDF -> document section AnalysisUnits."""
    raw = state.get("raw_input", {})
    pdf_path = str(raw.get("pdf_path", "specs/bnpl_credit_product_v1.pdf"))
    output_pdf_path = str(raw.get("output_pdf_path", "reports/bnpl_credit_findings.pdf"))

    units = [
        AnalysisUnit(
            unit_id="spec-sec-1",
            title="Section 3: Consumer Protection & Liability",
            content_type="spec_section",
            location=DocumentLocation(
                document_name=pdf_path,
                page_start=4,
                page_end=5,
                section_heading="3. Liability & Disputes",
            ),
            content=(
                "Customers purchasing retail goods between £150 and £5,000 using our regulated "
                "instalment credit agree to waive Section 75 claims against the lender and resolve "
                "all disputes solely with the merchant."
            ),
        ),
        AnalysisUnit(
            unit_id="spec-sec-2",
            title="Section 5: Cooling-Off Period",
            content_type="spec_section",
            location=DocumentLocation(
                document_name=pdf_path,
                page_start=8,
                page_end=8,
                section_heading="5. Cancellation & Withdrawal",
            ),
            content=(
                "Borrowers receive a 7-day cooling-off window after signing the credit agreement "
                "to cancel without penalty."
            ),
        ),
    ]

    analysis_input = AnalysisInput(
        target_id=pdf_path,
        source_type="product_spec",
        title=f"Product Specification Review: {pdf_path}",
        summary="New UK regulated retail instalment credit product specification.",
        units=units,
    )
    return {
        "analysis_input": analysis_input,
        "adapter_context": {
            "input_pdf_path": pdf_path,
            "output_pdf_path": output_pdf_path,
        },
    }
