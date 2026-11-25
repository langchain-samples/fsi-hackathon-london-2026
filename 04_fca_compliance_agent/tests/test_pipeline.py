"""End-to-end verification of the hexagonal compliance agent architecture.

Covers:
  1. All three swappable I/O modules (PR Review, Codebase Agentic Remediation Subgraph, Product Spec)
  2. Map-reduce triage filtering & parallel Send fan-out
  3. Zero-relevant-units edge case (compliant short-circuit)
  4. Optional HITL LangGraph interrupt() gate: approve, filter, and reject flows
  5. MCP driven adapter tool-argument mapping & response normalisation plugged into Core
  6. Static AST hexagonal boundary check ensuring domain/ports/core have zero adapter/infra imports
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from fca_compliance_agent import (
    AnalysisUnit,
    MCPConsumerCreditActAdapter,
    MCPFCAHandbookAdapter,
    MCPToolMapping,
    MockComplianceAnalyzer,
    MockConsumerCreditActAdapter,
    MockFCAHandbookAdapter,
    PullRequestDiffLocation,
    build_codebase_review_module,
    build_core_analysis_subgraph,
    build_pipeline_for_module,
    build_pr_review_module,
    build_product_spec_module,
)


def _build_mock_core():
    return build_core_analysis_subgraph(
        fca_port=MockFCAHandbookAdapter(),
        cca_port=MockConsumerCreditActAdapter(),
        analyzer_port=MockComplianceAnalyzer(),
    )


class TestHexagonalCompliancePipelines(unittest.IsolatedAsyncioTestCase):
    async def test_pr_review_module_end_to_end(self) -> None:
        pipeline = build_pipeline_for_module(
            build_pr_review_module(),
            core_subgraph=_build_mock_core(),
        )

        result = await pipeline.ainvoke(
            {
                "raw_input": {
                    "pr_url": "https://github.com/acme-bank/credit-engine/pull/77",
                    "pr_number": 77,
                    "repo": "acme-bank/credit-engine",
                    "head_sha": "deadbeef77",
                }
            }
        )

        report = result["findings_report"]
        deliverable = result["deliverable_result"]

        self.assertEqual(report.source_type, "pr_review")
        self.assertEqual(report.units_total, 3)
        self.assertEqual(report.units_triaged_in, 2)
        self.assertEqual(report.units_skipped, 1)  # button.css skipped
        self.assertEqual(report.overall_verdict, "non_compliant")
        self.assertEqual(len(report.findings), 2)
        self.assertEqual([f.finding_id for f in report.findings], ["F-001", "F-002"])

        # Both FCA & CCA citations grounded on every finding
        for finding in report.findings:
            self.assertTrue(finding.fca_citations, f"Missing FCA citations on {finding.title}")
            self.assertTrue(finding.cca_citations, f"Missing CCA citations on {finding.title}")
            self.assertTrue(finding.fca_vs_cca_comparison)

        # Outbound adapter received adapter_context pass-through
        self.assertEqual(deliverable.module_name, "pr_review")
        self.assertEqual(deliverable.status, "completed")
        self.assertEqual(deliverable.metadata["head_sha"], "deadbeef77")
        self.assertEqual(len(deliverable.metadata["pr_comments"]), 2)

        # Core private state keys must NOT leak into PipelineState
        self.assertNotIn("triaged_units", result)
        self.assertNotIn("unit_findings", result)

    async def test_codebase_review_agentic_outbound_subgraph(self) -> None:
        pipeline = build_pipeline_for_module(
            build_codebase_review_module(),
            core_subgraph=_build_mock_core(),
        )

        result = await pipeline.ainvoke(
            {
                "raw_input": {
                    "repo_url": "https://github.com/acme-bank/retail-lending",
                    "default_branch": "main",
                    "workspace_path": "/tmp/test-clone/retail-lending",
                }
            }
        )

        report = result["findings_report"]
        deliverable = result["deliverable_result"]

        self.assertEqual(report.source_type, "codebase")
        self.assertEqual(report.units_total, 3)
        self.assertEqual(report.units_skipped, 1)  # theme.css skipped
        self.assertEqual(report.overall_verdict, "non_compliant")
        self.assertEqual(len(report.findings), 2)

        # Verify multi-step agentic outbound subgraph ran and read workspace_path
        self.assertEqual(deliverable.module_name, "codebase_review")
        self.assertEqual(deliverable.status, "completed")
        self.assertEqual(
            deliverable.metadata["workspace_path"], "/tmp/test-clone/retail-lending"
        )
        self.assertEqual(
            set(deliverable.metadata["edited_files"]),
            {"lending/collections/default_notice.py", "lending/marketing/banner.py"},
        )
        # Outbound subgraph private keys must NOT leak into PipelineState
        self.assertNotIn("planned_edits", result)
        self.assertNotIn("applied_files", result)

    async def test_product_spec_module_end_to_end(self) -> None:
        pipeline = build_pipeline_for_module(
            build_product_spec_module(),
            core_subgraph=_build_mock_core(),
        )

        result = await pipeline.ainvoke(
            {
                "raw_input": {
                    "pdf_path": "specs/bnpl_v2.pdf",
                    "output_pdf_path": "reports/bnpl_v2_findings.pdf",
                }
            }
        )

        report = result["findings_report"]
        deliverable = result["deliverable_result"]

        self.assertEqual(report.source_type, "product_spec")
        self.assertEqual(report.overall_verdict, "non_compliant")
        self.assertEqual(len(report.findings), 2)
        cca_refs = {c.reference for f in report.findings for c in f.cca_citations}
        self.assertIn("CCA 1974 s.75", cca_refs)
        self.assertIn("CCA 1974 s.66A", cca_refs)

        self.assertEqual(deliverable.module_name, "product_spec_review")
        self.assertEqual(deliverable.artifacts, ["reports/bnpl_v2_findings.pdf"])

    async def test_zero_relevant_units_short_circuits_cleanly(self) -> None:
        pipeline = build_pipeline_for_module(
            build_pr_review_module(),
            core_subgraph=_build_mock_core(),
        )

        result = await pipeline.ainvoke(
            {
                "raw_input": {
                    "pr_url": "https://github.com/acme-bank/credit-engine/pull/88",
                    "units": [
                        AnalysisUnit(
                            unit_id="css-only",
                            title="static/theme.css",
                            content="body { margin: 0; }",
                            location=PullRequestDiffLocation(file_path="static/theme.css"),
                        )
                    ],
                }
            }
        )

        report = result["findings_report"]
        self.assertEqual(report.overall_verdict, "compliant")
        self.assertEqual(report.findings, [])
        self.assertEqual(report.units_triaged_in, 0)
        self.assertEqual(report.units_skipped, 1)

    async def test_hitl_gate_filter_and_reject(self) -> None:
        checkpointer = InMemorySaver()
        pipeline = build_pipeline_for_module(
            build_pr_review_module(),
            core_subgraph=_build_mock_core(),
            checkpointer=checkpointer,
        )

        # 1) Interrupt and resume with `filter` (approve only F-001)
        cfg_filter = {"configurable": {"thread_id": "thread-filter-1"}}
        paused = await pipeline.ainvoke(
            {"raw_input": {"pr_number": 101}, "require_human_approval": True},
            config=cfg_filter,
        )
        self.assertIn("__interrupt__", paused)
        self.assertNotIn("deliverable_result", paused)

        resumed = await pipeline.ainvoke(
            Command(
                resume={
                    "action": "filter",
                    "approved_finding_ids": ["F-001"],
                    "reviewer_notes": "Only escalate F-001 on this PR.",
                }
            ),
            config=cfg_filter,
        )
        self.assertEqual(len(resumed["findings_report"].findings), 1)
        self.assertEqual(resumed["findings_report"].findings[0].finding_id, "F-001")
        self.assertEqual(resumed["deliverable_result"].status, "completed")
        self.assertEqual(len(resumed["deliverable_result"].metadata["pr_comments"]), 1)

        # 2) Interrupt and resume with `reject` (outbound adapter must NOT execute)
        cfg_reject = {"configurable": {"thread_id": "thread-reject-1"}}
        paused_reject = await pipeline.ainvoke(
            {"raw_input": {"pr_number": 102}, "require_human_approval": True},
            config=cfg_reject,
        )
        self.assertIn("__interrupt__", paused_reject)

        aborted = await pipeline.ainvoke(
            Command(resume={"action": "reject", "reviewer_notes": "Already tracked elsewhere"}),
            config=cfg_reject,
        )
        self.assertEqual(aborted["deliverable_result"].status, "aborted_by_reviewer")
        self.assertEqual(aborted["deliverable_result"].module_name, "human_review_gate")

    async def test_mcp_adapters_plug_into_core_subgraph(self) -> None:
        calls: list[tuple[str, dict[str, Any]]] = []

        async def fake_fca_mcp(tool_name: str, args: dict[str, Any]) -> Any:
            calls.append((tool_name, args))
            # Simulate an official MCP CallToolResult containing JSON text content
            payload = {
                "results": [
                    {
                        "rule_ref": "CONC 4.2.5R",
                        "title": "Adequate explanations",
                        "content": "Lender must clearly explain the 14-day statutory right to withdraw.",
                        "url": "https://www.handbook.fca.org.uk/handbook/CONC/4/2.html",
                        "score": 0.95,
                    }
                ]
            }
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])

        async def fake_cca_mcp(tool_name: str, args: dict[str, Any]) -> Any:
            calls.append((tool_name, args))
            return [
                {
                    "section": "CCA 1974 s.66A",
                    "heading": "Right of withdrawal",
                    "text": "Debtor has 14 calendar days to withdraw from a regulated credit agreement.",
                }
            ]

        fca_mcp = MCPFCAHandbookAdapter(
            caller=fake_fca_mcp,
            tool_mapping=MCPToolMapping(
                search_tool="fca_rag_query",
                query_arg="q",
                top_k_arg="k",
            ),
        )
        cca_mcp = MCPConsumerCreditActAdapter(
            caller=fake_cca_mcp,
            tool_mapping=MCPToolMapping(
                search_tool="cca_lookup",
                fetch_tool="cca_get_section",
                query_arg="q",
                reference_arg="sec",
            ),
        )

        core = build_core_analysis_subgraph(
            fca_port=fca_mcp,
            cca_port=cca_mcp,
            analyzer_port=MockComplianceAnalyzer(),
        )
        pipeline = build_pipeline_for_module(build_pr_review_module(), core_subgraph=core)

        result = await pipeline.ainvoke(
            {
                "raw_input": {
                    "units": [
                        AnalysisUnit(
                            unit_id="u-1",
                            title="withdrawal.py",
                            content="withdrawal_days = 7",
                            location=PullRequestDiffLocation(file_path="withdrawal.py", end_line=1),
                        )
                    ]
                }
            }
        )

        report = result["findings_report"]
        self.assertEqual(len(report.findings), 1)
        self.assertEqual(report.findings[0].fca_citations[0].reference, "CONC 4.2.5R")
        self.assertEqual(report.findings[0].cca_citations[0].reference, "CCA 1974 s.66A")
        called_tools = {name for name, _ in calls}
        self.assertIn("fca_rag_query", called_tools)
        self.assertIn("cca_lookup", called_tools)
        self.assertIn("cca_get_section", called_tools)


class TestHexagonalArchitectureBoundaries(unittest.TestCase):
    """Static AST guard proving domain/, ports/, and core/ never depend on adapters or infra."""

    FORBIDDEN_PREFIXES = (
        "fca_compliance_agent.adapters",
        "adapters",
        "mcp",
        "langchain",
        "langchain_openai",
        "langchain_anthropic",
        "openai",
        "anthropic",
        "subprocess",
        "pypdf",
        "pdfplumber",
        "weasyprint",
    )

    def test_inward_dependency_rule(self) -> None:
        pkg_root = Path(__file__).resolve().parents[1] / "src" / "fca_compliance_agent"
        protected_dirs = [pkg_root / "domain", pkg_root / "ports", pkg_root / "core"]

        violations: list[str] = []
        for directory in protected_dirs:
            for py_file in directory.rglob("*.py"):
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            if any(
                                alias.name == bad or alias.name.startswith(f"{bad}.")
                                for bad in self.FORBIDDEN_PREFIXES
                            ):
                                violations.append(f"{py_file.name}: import {alias.name}")
                    elif isinstance(node, ast.ImportFrom):
                        mod = node.module or ""
                        if node.level > 0:
                            # Resolve relative imports: e.g. `from .adapters import ...` inside `ports/`
                            # is `ports.adapters`, NOT `fca_compliance_agent.adapters`.
                            rel_parts = py_file.relative_to(pkg_root).parts[:-1]
                            base = rel_parts[: max(0, len(rel_parts) - (node.level - 1))]
                            resolved = ".".join((*base, mod)) if mod else ".".join(base)
                        else:
                            resolved = mod
                        if any(
                            resolved == bad or resolved.startswith(f"{bad}.")
                            for bad in self.FORBIDDEN_PREFIXES
                        ):
                            violations.append(f"{py_file.name}: from {resolved} import ...")

        self.assertEqual(violations, [], f"Hexagonal boundary violations: {violations}")


if __name__ == "__main__":
    unittest.main()
