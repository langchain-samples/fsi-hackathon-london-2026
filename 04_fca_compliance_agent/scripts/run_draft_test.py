"""Live compliance runner using the real LLM configured in repo root `.env`.

Usage (from repo root, once `.env` is populated):
    # Scan a real GitHub repo (defaults to natchainsix/quicklend-nc)
    uv run --no-sync --package fca-compliance-agent python 04_fca_compliance_agent/scripts/run_draft_test.py
    uv run --no-sync --package fca-compliance-agent python 04_fca_compliance_agent/scripts/run_draft_test.py --repo-url https://github.com/natchainsix/quicklend-nc

    # Point at a running fca-regulatory-mcp HTTP server
    uv run --no-sync --package fca-compliance-agent python 04_fca_compliance_agent/scripts/run_draft_test.py --mcp-url http://127.0.0.1:8001/mcp

    # Review a real PR or run the built-in synthetic diff
    uv run --no-sync --package fca-compliance-agent python 04_fca_compliance_agent/scripts/run_draft_test.py --module pr_review
    uv run --no-sync --package fca-compliance-agent python 04_fca_compliance_agent/scripts/run_draft_test.py --module pr_review --pr-url https://github.com/owner/repo/pull/1

    # Product spec stub review
    uv run --no-sync --package fca-compliance-agent python 04_fca_compliance_agent/scripts/run_draft_test.py --module product_spec
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

_SYNTHETIC_PR_DIFF = """diff --git a/src/underwriting/approve.py b/src/underwriting/approve.py
index 1111111..2222222 100644
--- a/src/underwriting/approve.py
+++ b/src/underwriting/approve.py
@@ -18,4 +18,7 @@ def evaluate_loan_application(app):
+# Fast-track promotional campaign
+skip_affordability = True
+BANNER_COPY = 'Instant cash - guaranteed approval in 60 seconds!'
diff --git a/src/contracts/withdrawal.py b/src/contracts/withdrawal.py
index 3333333..4444444 100644
--- a/src/contracts/withdrawal.py
+++ b/src/contracts/withdrawal.py
@@ -5,2 +5,2 @@ def withdrawal_window():
-withdrawal_days = 14
+withdrawal_days = 7  # shortened cooling-off window
"""


def _build_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.module == "codebase_review":
        raw: dict[str, Any] = {
            "repo_url": args.repo_url,
            "default_branch": args.branch,
        }
        if args.workspace_path:
            raw["workspace_path"] = args.workspace_path
        return {"raw_input": raw}

    if args.module == "pr_review":
        if args.pr_url:
            return {"raw_input": {"pr_url": args.pr_url}}
        return {
            "raw_input": {
                "diff": _SYNTHETIC_PR_DIFF,
                "repo": "acme-bank/credit-engine",
                "pr_number": 42,
                "title": "PR #42: Fast-track personal loan onboarding (synthetic diff)",
            }
        }

    return {
        "raw_input": {
            "pdf_path": "specs/bnpl_credit_product_v1.pdf",
            "output_pdf_path": "reports/bnpl_credit_findings.pdf",
        }
    }


async def _run(args: argparse.Namespace) -> None:
    if args.mcp_url:
        os.environ["FCA_MCP_URL"] = args.mcp_url
        os.environ["CCA_MCP_URL"] = args.mcp_url

    from fca_compliance_agent.agent import (
        codebase_review_agent,
        pr_review_agent,
        product_spec_agent,
    )

    agents = {
        "codebase_review": codebase_review_agent,
        "pr_review": pr_review_agent,
        "product_spec": product_spec_agent,
    }
    agent = agents[args.module]
    payload = _build_payload(args)

    print(f"Running live LLM compliance check on module: {args.module} ...")
    if os.environ.get("CCA_MCP_URL"):
        print(f"Using CCA MCP server: {os.environ['CCA_MCP_URL']}")
    print()

    result = await agent.ainvoke(payload)

    report = result["findings_report"]
    deliverable = result["deliverable_result"]

    print(f"Report ID:       {report.report_id}")
    print(f"Verdict:         {report.overall_verdict}")
    print(
        f"Units:           {report.units_total} total | "
        f"{report.units_triaged_in} analysed | {report.units_skipped} skipped"
    )
    print(f"\nExecutive Summary:\n{report.executive_summary}\n")
    print(f"Findings ({len(report.findings)}):")
    for f in report.findings:
        print(
            json.dumps(
                {
                    "finding_id": f.finding_id,
                    "severity": f.severity,
                    "status": f.status,
                    "title": f.title,
                    "location": f.location.model_dump(),
                    "fca_citations": [c.reference for c in f.fca_citations],
                    "cca_citations": [c.reference for c in f.cca_citations],
                    "fca_vs_cca_comparison": f.fca_vs_cca_comparison,
                    "remediation_guidance": f.remediation_guidance,
                },
                indent=2,
            )
        )
    print(f"\nDeliverable Result:\n{deliverable.model_dump_json(indent=2)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a live LLM FCA/CCA compliance check.")
    parser.add_argument(
        "--module",
        choices=["codebase_review", "pr_review", "product_spec"],
        default="codebase_review",
        help="Which compliance module to run (default: codebase_review).",
    )
    parser.add_argument(
        "--repo-url",
        default="https://github.com/natchainsix/quicklend-nc",
        help="GitHub repository URL for codebase_review (default: quicklend-nc).",
    )
    parser.add_argument(
        "--branch",
        default="main",
        help="Branch to scan for codebase_review (default: main).",
    )
    parser.add_argument(
        "--workspace-path",
        default=None,
        help="Optional pre-cloned local directory for codebase_review (skips git clone).",
    )
    parser.add_argument(
        "--pr-url",
        default=None,
        help="GitHub PR URL for pr_review (if omitted, runs an offline synthetic diff).",
    )
    parser.add_argument(
        "--mcp-url",
        default=None,
        help="Optional Streamable HTTP MCP URL (sets FCA_MCP_URL and CCA_MCP_URL).",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
