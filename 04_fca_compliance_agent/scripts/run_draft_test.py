"""Live compliance runner using real integrations end-to-end.

Always runs against real integrations (no mocks or stubs):
  - LLM: real model configured in repo root `.env` (`LangChainComplianceAnalyzer` & `LangChainCodeRemediator`)
  - FCA Handbook RAG MCP: connects to `--fca-mcp-url` / `FCA_MCP_URL`, reuses a running local
    `handbook_rag/mcp_server.py` on `http://127.0.0.1:8765/mcp`, or auto-bootstraps it in the background
  - Consumer Credit Act 1974 MCP: connects to `--cca-mcp-url` / `CCA_MCP_URL`, or auto-bootstraps
    the local `fca-regulatory-mcp` HTTP server (backed by `cca.md`) in the background
  - GitHub I/O:
      - `codebase_review` scans the target GitHub repo, generates code remediations for any
        findings, pushes a `compliance/fca-cca-<report_id>` branch, and opens a real GitHub PR.
      - `pr_review` fetches a real GitHub PR (`--pr-url`) and posts a real PR review comment.

Pass `--no-github` to save local reports only (no PR creation / no PR comment) or
`--require-approval` for an interactive HITL approval prompt before GitHub writes.

Usage (recommended via wrapper script, which automates `uv` and extras):
    # Scan a GitHub repo and open a remediation PR (auto-bootstrapped MCP servers)
    ./04_fca_compliance_agent/scripts/run.sh
    ./04_fca_compliance_agent/scripts/run.sh --repo-url https://github.com/natchainsix/safelend-co --branch main

    # Open a draft remediation PR, or save local reports only without touching GitHub
    ./04_fca_compliance_agent/scripts/run.sh --repo-url https://github.com/natchainsix/safelend-co --draft-pr
    ./04_fca_compliance_agent/scripts/run.sh --repo-url https://github.com/natchainsix/safelend-co --no-github

    # Review a real PR and post a review comment
    ./04_fca_compliance_agent/scripts/run.sh --module pr_review --pr-url https://github.com/owner/repo/pull/1

    # Pause for interactive human approval before opening a PR / posting a PR comment
    ./04_fca_compliance_agent/scripts/run.sh --repo-url https://github.com/natchainsix/safelend-co --require-approval

    # Point at already-running external MCP HTTP servers instead of auto-bootstrapping
    ./04_fca_compliance_agent/scripts/run.sh \\
      --fca-mcp-url http://127.0.0.1:8765/mcp \\
      --cca-mcp-url http://127.0.0.1:8001/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
from typing import Any
from uuid import uuid4

from fca_compliance_agent.mcp_server.bootstrap import (
    DEFAULT_CCA_MCP_PORT,
    DEFAULT_FCA_MCP_PORT,
    managed_cca_mcp_server,
    managed_fca_mcp_server,
    resolve_cca_mcp_mode,
    resolve_fca_mcp_mode,
)


def _build_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.module == "codebase_review":
        if args.workspace_path:
            raw: dict[str, Any] = {
                "workspace_path": args.workspace_path,
                "default_branch": args.branch,
                "create_pr": False,
                "draft_pr": args.draft_pr,
            }
        else:
            raw = {
                "repo_url": args.repo_url,
                "default_branch": args.branch,
                "create_pr": not args.no_github,
                "draft_pr": args.draft_pr,
            }
        payload: dict[str, Any] = {"raw_input": raw}
        if args.require_approval:
            payload["require_human_approval"] = True
        return payload

    if args.module == "pr_review":
        if not args.pr_url:
            raise ValueError("pr_review requires --pr-url https://github.com/owner/repo/pull/N.")
        raw = {
            "pr_url": args.pr_url,
            "post_to_github": not args.no_github,
        }
        payload = {"raw_input": raw}
        if args.require_approval:
            payload["require_human_approval"] = True
        return payload

    raise ValueError(f"Unsupported module: {args.module!r}")


def _build_agent(module_name: str, *, require_approval: bool = False):
    from langgraph.checkpoint.memory import InMemorySaver

    from fca_compliance_agent.adapters.github_codebase import build_github_codebase_review_module
    from fca_compliance_agent.adapters.github_pr import build_github_pr_review_module
    from fca_compliance_agent.agent import build_default_core_subgraph
    from fca_compliance_agent.core import build_pipeline_for_module

    core = build_default_core_subgraph()
    module_builders = {
        "codebase_review": build_github_codebase_review_module,
        "pr_review": build_github_pr_review_module,
    }
    return build_pipeline_for_module(
        module_builders[module_name](),
        core_subgraph=core,
        checkpointer=InMemorySaver() if require_approval else None,
    )


def _prompt_human_review(interrupt_payload: dict[str, Any]) -> dict[str, Any]:
    print("\n=== Human Review Required Before Outbound Delivery ===")
    print(f"Report ID: {interrupt_payload.get('report_id')}")
    print(f"Target:    {interrupt_payload.get('target_id')}")
    print(f"Verdict:   {interrupt_payload.get('overall_verdict')}")
    print(f"\nSummary:\n{interrupt_payload.get('executive_summary')}\n")
    for item in interrupt_payload.get("findings") or []:
        refs = ", ".join((item.get("fca_refs") or []) + (item.get("cca_refs") or [])) or "none"
        print(
            f"  - {item.get('finding_id')} [{str(item.get('severity', '')).upper()}] "
            f"{item.get('title')} ({refs})"
        )

    raw_choice = input(
        "\nProceed? [a]pprove all / [f]ilter F-001,F-002 / [r]eject (default: approve): "
    ).strip()
    lowered = raw_choice.lower()
    if not lowered or lowered in ("a", "approve", "y", "yes"):
        return {"action": "approve", "reviewer_notes": "Approved via CLI"}
    if lowered in ("r", "reject", "n", "no"):
        return {"action": "reject", "reviewer_notes": "Rejected via CLI"}
    if lowered.startswith("f"):
        ids_part = raw_choice.split(None, 1)[1] if " " in raw_choice else ""
        ids = [token.strip() for token in ids_part.split(",") if token.strip()]
        return {"action": "filter", "approved_finding_ids": ids, "reviewer_notes": "Filtered via CLI"}
    return {"action": "reject", "reviewer_notes": f"Unrecognised CLI choice: {raw_choice}"}


async def _run(args: argparse.Namespace) -> None:
    from langgraph.types import Command

    from fca_compliance_agent.adapters.llm.langchain_llm import load_repo_env

    load_repo_env()
    payload = _build_payload(args)

    fca_external_url, should_bootstrap_fca = resolve_fca_mcp_mode(fca_mcp_url=args.fca_mcp_url)
    cca_external_url, should_bootstrap_cca = resolve_cca_mcp_mode(cca_mcp_url=args.cca_mcp_url)

    with contextlib.ExitStack() as stack:
        if should_bootstrap_fca:
            fca_port = args.fca_mcp_port if args.fca_mcp_port is not None else DEFAULT_FCA_MCP_PORT
            fca_url = stack.enter_context(
                managed_fca_mcp_server(
                    port=fca_port,
                    strict_port=args.fca_mcp_port is not None,
                )
            )
            os.environ["FCA_MCP_URL"] = fca_url
            print(f"Started background FCA Handbook RAG MCP server: {fca_url}")
        else:
            assert fca_external_url is not None
            os.environ["FCA_MCP_URL"] = fca_external_url
            print(f"Using FCA Handbook RAG MCP server: {fca_external_url}")

        if should_bootstrap_cca:
            cca_port = args.cca_mcp_port if args.cca_mcp_port is not None else DEFAULT_CCA_MCP_PORT
            cca_url = stack.enter_context(
                managed_cca_mcp_server(
                    port=cca_port,
                    strict_port=args.cca_mcp_port is not None,
                )
            )
            os.environ["CCA_MCP_URL"] = cca_url
            print(f"Started background CCA 1974 MCP server: {cca_url}")
        else:
            assert cca_external_url is not None
            os.environ["CCA_MCP_URL"] = cca_external_url
            print(f"Using CCA 1974 MCP server: {cca_external_url}")

        agent = _build_agent(
            args.module,
            require_approval=args.require_approval,
        )

        print(f"Running live LLM compliance check on module: {args.module} ...\n")
        if args.require_approval:
            config = {"configurable": {"thread_id": f"cli-{uuid4().hex[:8]}"}}
            result = await agent.ainvoke(payload, config)
            if interrupts := result.get("__interrupt__"):
                first = interrupts[0]
                interrupt_val = getattr(first, "value", first)
                decision = _prompt_human_review(
                    interrupt_val if isinstance(interrupt_val, dict) else {}
                )
                result = await agent.ainvoke(Command(resume=decision), config)
        else:
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
            "Run a live LLM FCA/CCA compliance check through real integrations. "
            "Connects to or auto-bootstraps the local FCA Handbook RAG MCP server "
            "(handbook_rag/mcp_server.py) and CCA 1974 MCP server (fca-regulatory-mcp). "
            "By default, codebase_review opens a real remediation PR and pr_review posts a real PR comment."
        )
    )
    parser.add_argument(
        "--module",
        choices=["codebase_review", "pr_review"],
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
        help="Optional pre-cloned local directory for codebase_review (skips git clone and PR creation).",
    )
    parser.add_argument(
        "--pr-url",
        default=None,
        help="GitHub PR URL for pr_review (required when --module pr_review).",
    )
    parser.add_argument(
        "--no-github",
        action="store_true",
        help="Save local reports only; skip opening a remediation PR or posting a PR comment.",
    )
    parser.add_argument(
        "--draft-pr",
        action="store_true",
        help="Open the codebase_review remediation PR as a GitHub draft PR.",
    )
    parser.add_argument(
        "--require-approval",
        action="store_true",
        help="Pause for interactive human approval before opening a PR or posting a PR comment.",
    )
    parser.add_argument(
        "--fca-mcp-url",
        default=None,
        help="Optional external Streamable HTTP URL for the FCA Handbook RAG MCP server.",
    )
    parser.add_argument(
        "--fca-mcp-port",
        type=int,
        default=None,
        help=(
            f"Optional port for the auto-bootstrapped FCA Handbook RAG MCP server "
            f"(default: {DEFAULT_FCA_MCP_PORT}, falling back to a free port if busy)."
        ),
    )
    parser.add_argument(
        "--cca-mcp-url",
        default=None,
        help="Optional external Streamable HTTP URL for the Consumer Credit Act 1974 MCP server.",
    )
    parser.add_argument(
        "--cca-mcp-port",
        type=int,
        default=None,
        help=(
            f"Optional port for the auto-bootstrapped CCA 1974 MCP server "
            f"(default: {DEFAULT_CCA_MCP_PORT}, falling back to a free port if busy)."
        ),
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
    except ValueError as exc:
        parser.error(str(exc))
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
