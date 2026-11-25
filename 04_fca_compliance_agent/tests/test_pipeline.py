"""End-to-end verification of the hexagonal compliance agent architecture.

Tests exercise the REAL inbound/outbound behaviour adapters (`build_github_pr_review_module`,
`build_github_codebase_review_module`) while injecting mock API adapters (`LocalMockGitHubClient`,
fake MCP callers, and deterministic test-only LLM doubles) that write outputs to disk and report
to stdout instead of calling external services.

Covers:
  1. Real PR Review & Codebase Remediation behaviour adapters backed by `LocalMockGitHubClient`
  2. Product Spec stub module (PDF adapter not yet implemented)
  3. Map-reduce triage filtering & parallel Send fan-out
  4. Zero-relevant-units edge case (compliant short-circuit)
  5. Optional HITL LangGraph interrupt() gate: filter and reject flows
  6. MCP driven adapter tool-argument mapping & response normalisation plugged into Core
  7. Static AST hexagonal boundary check ensuring domain/ports/core have zero adapter/infra imports
"""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from fakes import (
    LocalMockGitHubClient,
    MockConsumerCreditActAdapter,
    MockFCAHandbookAdapter,
    build_product_spec_stub_module,
)

from fca_compliance_agent import (
    AnalysisInput,
    AnalysisUnit,
    Finding,
    MCPConsumerCreditActAdapter,
    MCPFCAHandbookAdapter,
    MCPToolError,
    MCPToolMapping,
    RegulatoryCitation,
    RegulatoryPassage,
    TriagedUnit,
    build_core_analysis_subgraph,
    build_github_codebase_review_module,
    build_github_pr_review_module,
    build_pipeline_for_module,
)
from fca_compliance_agent.adapters.github_codebase import (
    FileRemediationResult,
    render_codebase_markdown,
    render_remediation_pr_body,
)
from fca_compliance_agent.adapters.github_pr import render_markdown

_SAMPLE_PR_DIFF = """diff --git a/src/underwriting/approve.py b/src/underwriting/approve.py
index 1111111..2222222 100644
--- a/src/underwriting/approve.py
+++ b/src/underwriting/approve.py
@@ -18,3 +18,5 @@ def evaluate_loan_application(app):
+skip_affordability = True
+BANNER_COPY = 'Instant cash - guaranteed approval in 60 seconds!'
diff --git a/src/contracts/withdrawal.py b/src/contracts/withdrawal.py
index 3333333..4444444 100644
--- a/src/contracts/withdrawal.py
+++ b/src/contracts/withdrawal.py
@@ -5,2 +5,2 @@ def withdrawal_window():
-withdrawal_days = 14
+withdrawal_days = 7  # shortened cooling-off window
diff --git a/static/button.css b/static/button.css
index 5555555..6666666 100644
--- a/static/button.css
+++ b/static/button.css
@@ -1,2 +1,2 @@
-.btn { color: blue; }
+.btn { color: navy; }
"""

_CSS_ONLY_PR_DIFF = """diff --git a/static/theme.css b/static/theme.css
index 1111111..2222222 100644
--- a/static/theme.css
+++ b/static/theme.css
@@ -1,1 +1,1 @@
-body { margin: 4px; }
+body { margin: 0; }
"""

_SINGLE_FILE_PR_DIFF = """diff --git a/withdrawal.py b/withdrawal.py
index 1111111..2222222 100644
--- a/withdrawal.py
+++ b/withdrawal.py
@@ -1,1 +1,1 @@
-withdrawal_days = 14
+withdrawal_days = 7
"""


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


class _DeterministicCodeRemediator:
    """Deterministic LLM double for `CodeRemediator` used alongside the real outbound behaviour."""

    async def remediate_file(
        self,
        *,
        file_path: str,
        original_content: str,
        findings: list[Finding],
        repo_summary: str = "",
    ) -> FileRemediationResult:
        updated = (
            original_content.replace("default_notice_days = 5", "default_notice_days = 14")
            .replace("hide_apr = True", "hide_apr = False")
        )
        if updated == original_content:
            updated = original_content + "\n# remediated\n"
        return FileRemediationResult(
            can_fix=True,
            updated_content=updated,
            change_summary=f"Applied {len(findings)} compliance fix(es) to {file_path}.",
        )


def _build_test_core():
    return build_core_analysis_subgraph(
        fca_port=MockFCAHandbookAdapter(),
        cca_port=MockConsumerCreditActAdapter(),
        analyzer_port=_FakeComplianceAnalyzer(),
    )


class TestHexagonalCompliancePipelines(unittest.IsolatedAsyncioTestCase):
    async def test_pr_review_module_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_github",
                pr_meta={
                    "title": "PR #77: Fast-track loan onboarding",
                    "body": "Speeds up onboarding.",
                    "headRefOid": "deadbeef77",
                },
                pr_diff=_SAMPLE_PR_DIFF,
            )
            pipeline = build_pipeline_for_module(
                build_github_pr_review_module(
                    github_client=mock_gh,
                    reports_dir=tmp_path / "reports",
                ),
                core_subgraph=_build_test_core(),
            )

            result = await pipeline.ainvoke(
                {"raw_input": {"pr_url": "https://github.com/acme-bank/credit-engine/pull/77"}}
            )
            summary = mock_gh.print_summary()

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
            self.assertTrue(deliverable.metadata["posted_to_github"])
            self.assertEqual(len(mock_gh.posted_comments), 1)
            self.assertTrue(Path(mock_gh.posted_comments[0]["body_path"]).is_file())
            self.assertIn("Comments: 1", summary)

            self.assertNotIn("triaged_units", result)
            self.assertNotIn("unit_findings", result)

    async def test_codebase_review_real_outbound_remediator_with_mock_github_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_github",
                repo_files={
                    "lending/collections/default_notice.py": "default_notice_days = 5\n",
                    "lending/marketing/banner.py": "hide_apr = True\n",
                    "static/theme.css": "body { color: black; }\n",
                },
            )
            pipeline = build_pipeline_for_module(
                build_github_codebase_review_module(
                    remediator=_DeterministicCodeRemediator(),
                    github_client=mock_gh,
                    reports_dir=tmp_path / "reports",
                ),
                core_subgraph=_build_test_core(),
            )

            result = await pipeline.ainvoke(
                {
                    "raw_input": {
                        "repo_url": "https://github.com/acme-bank/retail-lending",
                        "default_branch": "main",
                    }
                }
            )
            summary = mock_gh.print_summary()

            report = result["findings_report"]
            deliverable = result["deliverable_result"]

            self.assertEqual(report.source_type, "codebase")
            self.assertEqual(report.units_total, 3)
            self.assertEqual(report.units_skipped, 1)  # theme.css skipped
            self.assertEqual(report.overall_verdict, "non_compliant")
            self.assertEqual(len(report.findings), 2)

            self.assertEqual(deliverable.module_name, "codebase_review")
            self.assertEqual(deliverable.status, "completed")
            self.assertTrue(deliverable.metadata["pr_created"])
            self.assertEqual(
                set(deliverable.metadata["edited_files"]),
                {"lending/collections/default_notice.py", "lending/marketing/banner.py"},
            )
            self.assertEqual(len(mock_gh.created_prs), 1)
            self.assertTrue(Path(mock_gh.created_prs[0]["body_path"]).is_file())
            self.assertEqual(
                mock_gh.commits[0]["written_files"]["lending/collections/default_notice.py"].strip(),
                "default_notice_days = 14",
            )
            self.assertIn("PRs:      1", summary)

    async def test_custom_document_module_end_to_end(self) -> None:
        pipeline = build_pipeline_for_module(
            build_product_spec_stub_module(),
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
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_github",
                pr_diff=_CSS_ONLY_PR_DIFF,
            )
            pipeline = build_pipeline_for_module(
                build_github_pr_review_module(
                    github_client=mock_gh,
                    reports_dir=tmp_path / "reports",
                ),
                core_subgraph=_build_test_core(),
            )

            result = await pipeline.ainvoke(
                {"raw_input": {"pr_url": "https://github.com/acme-bank/credit-engine/pull/88"}}
            )

            report = result["findings_report"]
            self.assertEqual(report.overall_verdict, "compliant")
            self.assertEqual(report.findings, [])
            self.assertEqual(report.units_triaged_in, 0)
            self.assertEqual(report.units_skipped, 1)

    async def test_hitl_gate_filter_and_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_github",
                pr_diff=_SAMPLE_PR_DIFF,
            )
            checkpointer = InMemorySaver()
            pipeline = build_pipeline_for_module(
                build_github_pr_review_module(
                    github_client=mock_gh,
                    reports_dir=tmp_path / "reports",
                ),
                core_subgraph=_build_test_core(),
                checkpointer=checkpointer,
            )

            cfg_filter = {"configurable": {"thread_id": "thread-filter-1"}}
            paused = await pipeline.ainvoke(
                {
                    "raw_input": {"repo": "acme-bank/credit-engine", "pr_number": 101},
                    "require_human_approval": True,
                },
                config=cfg_filter,
            )
            self.assertIn("__interrupt__", paused)
            self.assertNotIn("deliverable_result", paused)
            self.assertEqual(mock_gh.posted_comments, [])

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
            self.assertTrue(resumed["deliverable_result"].metadata["posted_to_github"])
            self.assertEqual(len(mock_gh.posted_comments), 1)
            self.assertIn("**Findings:** 1", mock_gh.posted_comments[0]["body"])

            cfg_reject = {"configurable": {"thread_id": "thread-reject-1"}}
            paused_reject = await pipeline.ainvoke(
                {
                    "raw_input": {"repo": "acme-bank/credit-engine", "pr_number": 102},
                    "require_human_approval": True,
                },
                config=cfg_reject,
            )
            self.assertIn("__interrupt__", paused_reject)

            aborted = await pipeline.ainvoke(
                Command(resume={"action": "reject", "reviewer_notes": "Already tracked elsewhere"}),
                config=cfg_reject,
            )
            self.assertEqual(aborted["deliverable_result"].status, "aborted_by_reviewer")
            self.assertEqual(aborted["deliverable_result"].module_name, "human_review_gate")
            self.assertEqual(len(mock_gh.posted_comments), 1)  # still only the approved PR #101 comment

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

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_github",
                pr_diff=_SINGLE_FILE_PR_DIFF,
            )
            core = build_core_analysis_subgraph(
                fca_port=fca_mcp,
                cca_port=cca_mcp,
                analyzer_port=_FakeComplianceAnalyzer(),
            )
            pipeline = build_pipeline_for_module(
                build_github_pr_review_module(
                    github_client=mock_gh,
                    reports_dir=tmp_path / "reports",
                ),
                core_subgraph=core,
            )

            result = await pipeline.ainvoke(
                {"raw_input": {"pr_url": "https://github.com/acme-bank/credit-engine/pull/99"}}
            )

            report = result["findings_report"]
            self.assertEqual(len(report.findings), 1)
            self.assertEqual(report.findings[0].fca_citations[0].reference, "CONC 4.2.5R")
            self.assertEqual(report.findings[0].cca_citations[0].reference, "CCA 1974 s.66A")
            called_tools = {name for name, _ in calls}
            self.assertIn("fca_rag_query", called_tools)
            self.assertIn("cca_lookup", called_tools)
            self.assertIn("cca_get_section", called_tools)
            self.assertEqual(len(mock_gh.posted_comments), 1)


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

        name = "get_consumer_credit_act_document"
        missing = f"Error executing tool {name}: CCA 1974 has no section 999. Check the number."
        self.assertEqual(await failing(missing).get_document("999"), [])
        for text in (f"Unknown tool: {name}", f"Error executing tool {name}", "Server is overloaded"):
            with self.subTest(text), self.assertRaises(MCPToolError):
                await failing(text).get_document("66A")

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


def _rag(tools: dict[str, Any], calls: list[str] | None = None):
    """A caller for a server with these tools. A result that is a string is that tool's error."""

    async def caller(name: str, arguments: dict[str, Any]) -> Any:
        if calls is not None:
            calls.append(name)
        result = tools.get(name, f"Unknown tool: {name}")
        if isinstance(result, str):
            return SimpleNamespace(is_error=True, content=[SimpleNamespace(text=result)], structured_content=None)
        return SimpleNamespace(is_error=False, content=[], structured_content=result)

    return caller


class TestHandbookRAGServer(unittest.IsolatedAsyncioTestCase):
    """Servers offer different tools: the Handbook RAG has no get_fca_rule, and others have no get_fca_section."""

    def _section(self, *references: str) -> dict[str, Any]:
        rows = [{"reference": r, "text": f"Text of {r}", "provision_type": r[-1]} for r in references]
        return {"section": "CONC 5.2A", "complete": True, "results": rows}

    async def test_get_rule_reads_the_section_and_never_gives_a_neighbour(self) -> None:
        fca = MCPFCAHandbookAdapter(
            caller=_rag({"get_fca_section": self._section("CONC 5.2A.3R", "CONC 5.2A.4R", "CONC 5.2A.4AR")})
        )
        for reference, expected in (
            ("CONC 5.2A.4R", "CONC 5.2A.4R"),
            ("conc 5.2A.4 r", "CONC 5.2A.4R"),
            ("CONC 5.2A.4", "CONC 5.2A.4R"),
            ("CONC 5.2A", None),
            ("CONC 5.2A.9R", None),
        ):
            with self.subTest(reference):
                rule = await fca.get_rule(reference)
                self.assertEqual(rule and rule.reference, expected)

    async def test_get_rule_tells_a_missing_rule_from_a_failure(self) -> None:
        missing = "Error executing tool get_fca_section: CONC 9.9.9R is not in force in the index. Do not cite it."
        self.assertIsNone(await MCPFCAHandbookAdapter(caller=_rag({"get_fca_section": missing})).get_rule("CONC 9.9.9R"))
        for failure in ("There is no Handbook index called 'x'.", "Error executing tool get_fca_section"):
            with self.subTest(failure), self.assertRaises(MCPToolError):
                await MCPFCAHandbookAdapter(caller=_rag({"get_fca_section": failure})).get_rule("CONC 5.2A.4R")

    async def test_get_rule_uses_a_rule_tool_that_the_mapping_names(self) -> None:
        async def ask(tools: dict[str, Any]) -> RegulatoryPassage | None:
            mapping = MCPToolMapping(
                search_tool="search_fca_handbook", fetch_tool="get_fca_rule", section_tool="get_fca_section"
            )
            return await MCPFCAHandbookAdapter(caller=_rag(tools), tool_mapping=mapping).get_rule("CONC 5.2A.4R")

        section = self._section("CONC 5.2A.3R", "CONC 5.2A.4R")
        found = {"results": [{"reference": "CONC 5.2A.4R", "text": "From the rule tool."}]}
        self.assertEqual((await ask({"get_fca_rule": found})).text, "From the rule tool.")
        self.assertEqual((await ask({"get_fca_section": section})).reference, "CONC 5.2A.4R")  # the server lacks the tool
        with self.assertRaises(MCPToolError):  # the tool exists, and failed
            await ask({"get_fca_rule": "boom", "get_fca_section": section})

    async def test_a_custom_mapping_only_calls_the_tools_it_names(self) -> None:
        calls: list[str] = []
        rows = {"results": [{"reference": "CONC 1.1.1R", "text": "Cut short [...]"}]}
        fca = MCPFCAHandbookAdapter(
            caller=_rag({"my_search": rows}, calls), tool_mapping=MCPToolMapping(search_tool="my_search")
        )
        self.assertEqual([p.text for p in await fca.search_rules("x")], ["Cut short [...]"])
        self.assertEqual(calls, ["my_search"])
        for call in (lambda: fca.get_section("CONC 1.1"), lambda: fca.check_citation("CONC 1.1.1R", "x")):
            with self.assertRaises(MCPToolError):
                await call()

    async def test_every_envelope_gives_the_same_passages(self) -> None:
        rows = [{"reference": "CONC 1.1.1R", "text": "A firm must."}]
        body = {"results": rows, "not_in_index": ["DISP"]}
        block = {"type": "text", "text": json.dumps(body)}
        for envelope in (
            body,
            {"isError": False, "content": [block]},
            {"content": [], "structuredContent": body},
            SimpleNamespace(is_error=False, content=[], structured_content=body),
            SimpleNamespace(isError=False, content=[], structuredContent=body),
            SimpleNamespace(is_error=False, content=[SimpleNamespace(text=block["text"])], structured_content=None),
        ):

            async def caller(name: str, arguments: dict[str, Any], result: Any = envelope) -> Any:
                return result

            with self.subTest(envelope):
                fca = MCPFCAHandbookAdapter(caller=caller)
                found = await fca.search("x")
                self.assertEqual(([p.text for p in found.passages], found.not_in_index), (["A firm must."], ["DISP"]))
                self.assertEqual((await fca.search_rules_in_scope("x")).not_in_index, ["DISP"])
                self.assertEqual([p.text for p in await fca.search_rules("x")], ["A firm must."])

    async def test_lists_and_plain_passages_still_work(self) -> None:
        row = {"reference": "CONC 1.1.1R", "content": "A firm must."}
        for payload in (
            row,
            [row],
            SimpleNamespace(is_error=False, content=[], structured_content={"result": [row]}),
        ):

            async def caller(name: str, arguments: dict[str, Any], result: Any = payload) -> Any:
                return result

            with self.subTest(payload):
                passages = await MCPFCAHandbookAdapter(caller=caller).search_rules("x")
                self.assertEqual([p.text for p in passages], ["A firm must."])

    async def test_a_result_without_the_expected_keys_is_an_error(self) -> None:
        fca = MCPFCAHandbookAdapter(
            caller=_rag({n: {"other": 1} for n in ("search_fca_handbook", "get_fca_section", "check_fca_citation")})
        )
        for call in (
            lambda: fca.search("x"),
            lambda: fca.get_section("CONC 1.1"),
            lambda: fca.check_citation("CONC 1.1.1R", "x"),
        ):
            with self.assertRaises(MCPToolError):
                await call()


class TestScopeThatWasNotSearched(unittest.IsolatedAsyncioTestCase):
    """A report must say which part of its scope no rule was looked up in."""

    async def _report(self, scope: list[str], not_in_index: list[str]):
        class NoFindings(_FakeComplianceAnalyzer):
            async def analyze_unit(self, **_: Any) -> list[Finding]:
                return []

        body = {"results": [], "not_in_index": not_in_index}
        core = build_core_analysis_subgraph(
            fca_port=MCPFCAHandbookAdapter(caller=_rag({"search_fca_handbook": body})),
            cca_port=MockConsumerCreditActAdapter(),
            analyzer_port=NoFindings(),
        )
        unit = AnalysisUnit(
            unit_id="u1",
            title="Loan approval",
            content="skip_affordability = True",
            location={"kind": "generic", "reference": "x"},
        )
        given = AnalysisInput(
            target_id="t", source_type="codebase", title="t", units=[unit], regulatory_scope=scope
        )
        return (await core.ainvoke({"analysis_input": given}))["findings_report"]

    async def test_part_of_the_scope(self) -> None:
        report = await self._report(
            ["CONC", "UK GDPR", "Consumer Credit Act 1974"], ["UK GDPR", "CONSUMER CREDIT ACT 1974"]
        )
        self.assertEqual((report.overall_verdict, report.scope_not_searched), ("compliant", ["UK GDPR"]))
        for text in (
            render_markdown(report),
            render_codebase_markdown(report),
            render_remediation_pr_body(report, change_summaries={}),
        ):
            self.assertIn("**Not checked:** UK GDPR", text)

    async def test_none_of_the_fca_scope(self) -> None:
        report = await self._report(["DISP", "Consumer Credit Act 1974"], ["DISP", "CONSUMER CREDIT ACT 1974"])
        self.assertEqual((report.overall_verdict, report.scope_not_searched), ("requires_manual_review", ["DISP"]))

    async def test_all_of_the_scope(self) -> None:
        for scope, missing in ((["CONC"], []), (["Consumer Credit Act 1974"], ["CONSUMER CREDIT ACT 1974"])):
            report = await self._report(scope, missing)
            self.assertEqual((report.overall_verdict, report.scope_not_searched), ("compliant", []))
            self.assertNotIn("Not checked", render_markdown(report))


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

    def test_no_stubs_or_mocks_in_production_code(self) -> None:
        """Guardrail ensuring stubs and mocks live in tests/ only and never in src/ or scripts/."""
        import fca_compliance_agent

        project_root = Path(__file__).resolve().parents[1]
        src_root = project_root / "src" / "fca_compliance_agent"
        scripts_root = project_root / "scripts"

        bad_files = [
            str(p.relative_to(project_root))
            for p in src_root.rglob("*.py")
            if "stub" in p.stem.lower() or "mock" in p.stem.lower()
        ]
        self.assertEqual(bad_files, [], f"Stub/mock modules found in src/: {bad_files}")

        bad_defs: list[str] = []
        for directory in (src_root, scripts_root):
            for py_file in directory.rglob("*.py"):
                tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        lower = node.name.lower()
                        if "stub" in lower or "mock" in lower:
                            bad_defs.append(f"{py_file.relative_to(project_root)}:{node.name}")
        self.assertEqual(bad_defs, [], f"Stub/mock definitions found in production code: {bad_defs}")

        bad_exports = [
            name
            for name in fca_compliance_agent.__all__
            if "mock" in name.lower() or "stub" in name.lower() or "product_spec" in name.lower()
        ]
        self.assertEqual(bad_exports, [], f"Stub/mock symbols exported from package: {bad_exports}")


if __name__ == "__main__":
    unittest.main()
