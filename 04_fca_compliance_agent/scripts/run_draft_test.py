"""Draft live smoke test runner using the real LLM configured in repo root `.env`.

Usage (from repo root, once `.env` is populated):
    uv run --no-sync --package fca-compliance-agent python 04_fca_compliance_agent/scripts/run_draft_test.py
    uv run --no-sync --package fca-compliance-agent python 04_fca_compliance_agent/scripts/run_draft_test.py --module pr_review
    uv run --no-sync --package fca-compliance-agent python 04_fca_compliance_agent/scripts/run_draft_test.py --module product_spec
"""

from __future__ import annotations

import argparse
import asyncio
import json

from fca_compliance_agent.agent import (
    codebase_review_agent,
    pr_review_agent,
    product_spec_agent,
)

_MODULES = {
    "codebase_review": (
        codebase_review_agent,
        {
            "raw_input": {
                "repo_url": "https://github.com/acme-bank/retail-lending",
                "default_branch": "main",
                "workspace_path": "/tmp/stub-workspaces/retail-lending",
            }
        },
    ),
    "pr_review": (
        pr_review_agent,
        {
            "raw_input": {
                "pr_url": "https://github.com/acme-bank/credit-engine/pull/42",
                "pr_number": 42,
                "repo": "acme-bank/credit-engine",
            }
        },
    ),
    "product_spec": (
        product_spec_agent,
        {
            "raw_input": {
                "pdf_path": "specs/bnpl_credit_product_v1.pdf",
                "output_pdf_path": "reports/bnpl_credit_findings.pdf",
            }
        },
    ),
}


async def _run(module_name: str) -> None:
    agent, payload = _MODULES[module_name]
    print(f"Running live LLM compliance draft test on module: {module_name} ...\n")
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
    parser = argparse.ArgumentParser(description="Run a live LLM draft test against a stub module.")
    parser.add_argument(
        "--module",
        choices=list(_MODULES.keys()),
        default="codebase_review",
        help="Which stub module to run against the live LLM (default: codebase_review).",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.module))


if __name__ == "__main__":
    main()
