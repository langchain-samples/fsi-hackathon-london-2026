"""End-to-end verification of the hexagonal compliance agent architecture.

Covers:
  1. All three swappable I/O modules (PR Review, Codebase Agentic Remediation Subgraph, Product Spec)
  2. Map-reduce triage filtering & parallel Send fan-out
  3. Zero-relevant-units edge case (compliant short-circuit)
  4. Optional HITL LangGraph interrupt() gate: approve, filter, and reject flows
  5. MCP driven adapter tool-argument mapping & response normalisation plugged into Core
  6. Static AST hexagonal boundary check ensuring domain/ports/core have zero adapter/infra imports

Note: Unit tests inject a lightweight test-only `_FakeComplianceAnalyzer` implementing
`ComplianceAnalyzerPort` so CI stays deterministic and secret-free. The production package
uses `LangChainComplianceAnalyzer` directly via `.env`.
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
    AnalysisInput,
    AnalysisUnit,
    Finding,
    MCPConsumerCreditActAdapter,
    MCPFCAHandbookAdapter,
    MCPToolError,
    MCPToolMapping,
    MockConsumerCreditActAdapter,
    MockFCAHandbookAdapter,
    PullRequestDiffLocation,
    RegulatoryCitation,
    RegulatoryPassage,
    TriagedUnit,
    build_codebase_review_module,
    build_core_analysis_subgraph,
    build_pipeline_for_module,
    build_pr_review_module,
    build_product_spec_module,
)


class _FakeComplianceAnalyzer:
    """Test-only deterministic fake implementing `ComplianceAnalyzerPort` for offline CI."""

    _NOISE = (".css", ".svg", "package-lock", "favicon")
    _RULES = (
        ("guaranteed approval", "critical", "CONC 5.2A", "CCA 1974 s.140A", "Missing affordability / misleading claim"),
        ("skip_affordability", "critical", "CONC 5.2A", "CCA 1974 s.140A", "Missing affordability check"),
        ("withdrawal_days = 7", "critical", "CONC 4.2", "CCA 1974 s.66A", "Withdrawal window shorter than 14 days"),
        ("7-day cooling", "critical", "CONC 4.2", "CCA 1974 s.66A", "Cooling-off window shorter than 14 days"),
        ("default_notice_days = 5", "high", "CONC 7.3", "CCA 1974 s.87", "Default notice cure period under 14 days"),
        ("waive section 75", "high", "PRIN 2A", "CCA 1974 s.75", "Attempted waiver of Section 75 liability"),
        ("hide_apr", "medium", "CONC 3.5", "CCA 1974 s.55", "Representative APR omitted or hidden"),
    )

    async def triage_units(self, analysis_input: AnalysisInput) -> list[TriagedUnit]:
        decisions: list[TriagedUnit] = []
        for unit in analysis_input.units:
            meta = f"{unit.title} {unit.location.model_dump()}".lower()
            if any(m in meta for m in self._NOISE):
                decisions.append(TriagedUnit(unit_id=unit.unit_id, is_relevant=False, rationale="Noise asset"))
                continue
            sections: list[str] = []
            low = unit.content.lower()
            if "withdraw" in low or "cooling" in low:
                sections.append("s.66A")
            if "default" in low:
                sections.append("s.87")
            if "section 75" in low:
                sections.append("s.75")
            decisions.append(
                TriagedUnit(
                    unit_id=unit.unit_id,
                    is_relevant=True,
                    rationale="Credit/compliance relevant",
                    fca_queries=[f"{unit.title} {unit.content[:120]}"],
                    cca_queries=[f"{unit.title} {unit.content[:120]}"],
                    cca_sections=sections,
                )
            )
        return decisions

    async def analyze_unit(
        self,
        *,
        unit: AnalysisUnit,
        analysis_input_summary: str,
        fca_passages: list[RegulatoryPassage],
        cca_passages: list[RegulatoryPassage],
    ) -> list[Finding]:
        low = unit.content.lower()
        findings: list[Finding] = []
        seen_titles: set[str] = set()
        for trigger, severity, fca_pref, cca_pref, title in self._RULES:
            if trigger not in low or title in seen_titles:
                continue
            seen_titles.add(title)
            fca_match = [p for p in fca_passages if p.reference.upper().startswith(fca_pref.upper())] or fca_passages[:1]
            cca_match = [p for p in cca_passages if p.reference.upper().startswith(cca_pref.upper())] or cca_passages[:1]
            findings.append(
                Finding(
                    unit_id=unit.unit_id,
                    title=title,
                    description=f"Triggered on '{trigger}' in {unit.title} ({analysis_input_summary[:60]}).",
                    severity=severity,  # type: ignore[arg-type]
                    status="non_compliant",
                    location=unit.location,
                    evidence_excerpt=unit.content.strip()[:200],
                    fca_citations=[
                        RegulatoryCitation(source=p.source, reference=p.reference, excerpt=p.text[:120], url=p.url)
                        for p in fca_match
                    ],
                    cca_citations=[
                        RegulatoryCitation(source=p.source, reference=p.reference, excerpt=p.text[:120], url=p.url)
                        for p in cca_match
                    ],
                    fca_vs_cca_comparison=f"Cross-checked {fca_pref} against {cca_pref}.",
                    remediation_guidance=f"Remediate '{trigger}' to satisfy {fca_pref} and {cca_pref}.",
                )
            )
            break
        return findings

    async def synthesize_executive_summary(
        self,
        *,
        analysis_input: AnalysisInput,
        findings: list[Finding],
    ) -> str:
        if not findings:
            return f"No FCA/CCA findings for '{analysis_input.title}'."
        return f"Found {len(findings)} FCA/CCA finding(s) in '{analysis_input.title}'."


def _build_test_core():
    return build_core_analysis_subgraph(
        fca_port=MockFCAHandbookAdapter(),
        cca_port=MockConsumerCreditActAdapter(),
        analyzer_port=_FakeComplianceAnalyzer(),
    )


class TestHexagonalCompliancePipelines(unittest.IsolatedAsyncioTestCase):
    async def test_pr_review_module_end_to_end(self) -> None:
        pipeline = build_pipeline_for_module(
            build_pr_review_module(),
            core_subgraph=_build_test_core(),
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

        for finding in report.findings:
            self.assertTrue(finding.fca_citations, f"Missing FCA citations on {finding.title}")
            self.assertTrue(finding.cca_citations, f"Missing CCA citations on {finding.title}")
            self.assertTrue(finding.fca_vs_cca_comparison)

        self.assertEqual(deliverable.module_name, "pr_review")
        self.assertEqual(deliverable.status, "completed")
        self.assertEqual(deliverable.metadata["head_sha"], "deadbeef77")
        self.assertEqual(len(deliverable.metadata["pr_comments"]), 2)

        self.assertNotIn("triaged_units", result)
        self.assertNotIn("unit_findings", result)

    async def test_codebase_review_agentic_outbound_subgraph(self) -> None:
        pipeline = build_pipeline_for_module(
            build_codebase_review_module(),
            core_subgraph=_build_test_core(),
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

        self.assertEqual(deliverable.module_name, "codebase_review")
        self.assertEqual(deliverable.status, "completed")
        self.assertEqual(
            deliverable.metadata["workspace_path"], "/tmp/test-clone/retail-lending"
        )
        self.assertEqual(
            set(deliverable.metadata["edited_files"]),
            {"lending/collections/default_notice.py", "lending/marketing/banner.py"},
        )
        self.assertNotIn("planned_edits", result)
        self.assertNotIn("applied_files", result)

    async def test_product_spec_module_end_to_end(self) -> None:
        pipeline = build_pipeline_for_module(
            build_product_spec_module(),
            core_subgraph=_build_test_core(),
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
            core_subgraph=_build_test_core(),
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
            core_subgraph=_build_test_core(),
            checkpointer=checkpointer,
        )

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
            analyzer_port=_FakeComplianceAnalyzer(),
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


class TestMCPErrorsAreNotEvidence(unittest.IsolatedAsyncioTestCase):
    """A failed tool call must never become a passage, nor look like "no rules found"."""

    def _failures(self) -> list[Any]:
        block = SimpleNamespace(type="text", text="Unknown tool: search_fca_handbook")
        return [
            SimpleNamespace(is_error=True, content=[block], structured_content=None),  # mcp 2.x
            SimpleNamespace(isError=True, content=[block], structuredContent=None),  # mcp 1.x
            {"isError": True, "content": [{"type": "text", "text": block.text}]},  # raw JSON
            SimpleNamespace(is_error=True, content=[], structured_content=None),  # no message
        ]

    async def test_every_adapter_method_raises(self) -> None:
        for failure in self._failures():

            async def caller(name: str, arguments: dict[str, Any], result: Any = failure) -> Any:
                return result

            fca = MCPFCAHandbookAdapter(caller=caller)
            cca = MCPConsumerCreditActAdapter(caller=caller)
            for call in (
                lambda: fca.search_rules("affordability"),
                lambda: fca.get_rule("CONC 5.2A.4R"),
                lambda: cca.query_provisions("withdrawal"),
                lambda: cca.get_document(),
            ):
                with self.subTest(failure=failure), self.assertRaises(MCPToolError):
                    await call()

    async def test_a_guessed_cca_section_does_not_stop_the_review(self) -> None:
        def failing(text: str) -> Any:
            async def caller(name: str, arguments: dict[str, Any]) -> Any:
                return SimpleNamespace(is_error=True, content=[SimpleNamespace(text=text)])

            return MCPConsumerCreditActAdapter(caller=caller)

        self.assertEqual(await failing("Error executing tool: no section 999").get_document("999"), [])
        with self.assertRaises(MCPToolError):
            await failing("Unknown tool: get_consumer_credit_act_document").get_document("66A")

    async def test_success_and_empty_results_still_work(self) -> None:
        payload = json.dumps([{"reference": "CONC 5.2A.4R", "text": "An error in the rule text."}])
        ok = SimpleNamespace(is_error=False, content=[SimpleNamespace(text=payload)])

        async def found(name: str, arguments: dict[str, Any]) -> Any:
            return ok

        async def nothing(name: str, arguments: dict[str, Any]) -> Any:
            return SimpleNamespace(is_error=False, content=[])

        passages = await MCPFCAHandbookAdapter(caller=found).search_rules("affordability")
        self.assertEqual([p.reference for p in passages], ["CONC 5.2A.4R"])
        self.assertEqual(await MCPFCAHandbookAdapter(caller=nothing).search_rules("x"), [])


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
