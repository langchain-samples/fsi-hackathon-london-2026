"""Live compliance runner using the real LLM configured in repo root `.env`.

By default, automatically starts the local `fca-regulatory-mcp` HTTP server in the
background and tears it down cleanly when the run finishes.

Usage (recommended via wrapper script, which also automates `uv` and `--extra mcp`):
    # Scan the default GitHub repo (natchainsix/quicklend-nc) with auto-bootstrapped MCP
    ./04_fca_compliance_agent/scripts/run.sh
    ./04_fca_compliance_agent/scripts/run.sh --repo-url https://github.com/natchainsix/quicklend-nc

    # Point at an already-running external MCP HTTP server instead of auto-bootstrapping
    ./04_fca_compliance_agent/scripts/run.sh --mcp-url http://127.0.0.1:8001/mcp

    # Run offline against in-memory mock regulatory adapters (no MCP server)
    ./04_fca_compliance_agent/scripts/run.sh --no-mcp

    # Review a real PR or run the built-in synthetic diff
    ./04_fca_compliance_agent/scripts/run.sh --module pr_review
    ./04_fca_compliance_agent/scripts/run.sh --module pr_review --pr-url https://github.com/owner/repo/pull/1

    # Product spec stub review
    ./04_fca_compliance_agent/scripts/run.sh --module product_spec
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
from typing import Any

from fca_compliance_agent.mcp_server.bootstrap import (
    DEFAULT_MCP_PORT,
    managed_mcp_server,
    resolve_mcp_mode,
)

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


def _build_agent(module_name: str, *, use_mocks: bool):
    from fca_compliance_agent.adapters.github_codebase import build_github_codebase_review_module
    from fca_compliance_agent.adapters.github_pr import build_github_pr_review_module
    from fca_compliance_agent.adapters.outbound import build_product_spec_module
    from fca_compliance_agent.adapters.regulatory import (
        MockConsumerCreditActAdapter,
        MockFCAHandbookAdapter,
    )
    from fca_compliance_agent.agent import build_default_core_subgraph
    from fca_compliance_agent.core import build_pipeline_for_module

    if use_mocks:
        core = build_default_core_subgraph(
            fca_port=MockFCAHandbookAdapter(),
            cca_port=MockConsumerCreditActAdapter(),
        )
    else:
        core = build_default_core_subgraph()

    module_builders = {
        "codebase_review": build_github_codebase_review_module,
        "pr_review": build_github_pr_review_module,
        "product_spec": build_product_spec_module,
    }
    return build_pipeline_for_module(module_builders[module_name](), core_subgraph=core)


async def _run(args: argparse.Namespace) -> None:
    from fca_compliance_agent.adapters.llm.langchain_llm import load_repo_env

    load_repo_env()
    external_url, should_bootstrap = resolve_mcp_mode(
        mcp_url=args.mcp_url,
        no_mcp=args.no_mcp,
    )

    with contextlib.ExitStack() as stack:
        if args.no_mcp:
            print("Regulatory adapters: in-memory mock adapters (--no-mcp)")
        elif should_bootstrap:
            preferred_port = args.mcp_port if args.mcp_port is not None else DEFAULT_MCP_PORT
            mcp_url = stack.enter_context(
                managed_mcp_server(
                    port=preferred_port,
                    strict_port=args.mcp_port is not None,
                )
            )
            os.environ["FCA_MCP_URL"] = mcp_url
            os.environ["CCA_MCP_URL"] = mcp_url
            print(f"Started background regulatory MCP server: {mcp_url}")
        else:
            if args.mcp_url:
                os.environ["FCA_MCP_URL"] = args.mcp_url
                os.environ["CCA_MCP_URL"] = args.mcp_url
            print(f"Using regulatory MCP server: {external_url}")

        agent = _build_agent(args.module, use_mocks=args.no_mcp)
        payload = _build_payload(args)

        print(f"Running live LLM compliance check on module: {args.module} ...\n")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a live LLM FCA/CCA compliance check. "
            "Automatically starts the local regulatory MCP server in the background unless "
            "--mcp-url, --no-mcp, or FCA_MCP_URL/CCA_MCP_URL is provided."
        )
    )
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
        help="Optional external Streamable HTTP MCP URL (skips auto-bootstrapping a background MCP server).",
    )
    parser.add_argument(
        "--mcp-port",
        type=int,
        default=None,
        help=(
            f"Optional port for the auto-bootstrapped background MCP server "
            f"(default: {DEFAULT_MCP_PORT}, falling back to a free port if busy)."
        ),
    )
    parser.add_argument(
        "--no-mcp",
        action="store_true",
        help="Skip MCP server bootstrapping and use in-memory mock regulatory adapters.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    def _handle_sigterm(_signum: int, _frame: Any) -> None:
        raise SystemExit(143)

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
