"""Tests for the real Codebase Review adapter, MCP error handling, and env wiring (offline)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from test_pipeline import _FakeComplianceAnalyzer

from fca_compliance_agent import (
    MockConsumerCreditActAdapter,
    MockFCAHandbookAdapter,
    build_core_analysis_subgraph,
    build_pipeline_for_module,
)
from fca_compliance_agent.adapters import github_codebase
from fca_compliance_agent.adapters.github_codebase import (
    build_github_codebase_review_module,
    collect_codebase_units,
)
from fca_compliance_agent.adapters.regulatory.mcp import parse_mcp_passages


def _pipeline():
    core = build_core_analysis_subgraph(
        fca_port=MockFCAHandbookAdapter(),
        cca_port=MockConsumerCreditActAdapter(),
        analyzer_port=_FakeComplianceAnalyzer(),
    )
    return build_pipeline_for_module(build_github_codebase_review_module(), core_subgraph=core)


class CollectCodebaseUnitsTest(unittest.TestCase):
    def test_collects_code_and_config_skips_noise_and_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("# Sample Lending Repo\nReview scope.", encoding="utf-8")
            (root / "default_notice.py").write_text("default_notice_days = 5\n", encoding="utf-8")
            (root / "config.json").write_text('{"apr": 19.9}\n', encoding="utf-8")
            (root / "uv.lock").write_text("lock-data\n", encoding="utf-8")
            (root / "logo.bin").write_bytes(b"\x89PNG\r\n\x00\x01binary")
            git_dir = root / ".git"
            git_dir.mkdir()
            (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

            units, summary, skipped = collect_codebase_units(root)

        self.assertIn("Sample Lending Repo", summary)
        by_title = {u.title: u for u in units}
        self.assertEqual(set(by_title.keys()), {"README.md", "config.json", "default_notice.py"})
        self.assertEqual(by_title["default_notice.py"].content_type, "code")
        self.assertEqual(by_title["config.json"].content_type, "config")
        self.assertEqual(by_title["README.md"].content_type, "other")
        self.assertEqual(skipped, 2)  # uv.lock + logo.bin


class GithubCodebaseAdapterTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.git = mock.patch.object(github_codebase, "_git").start()
        self.addCleanup(mock.patch.stopall)

    async def test_local_workspace_runs_end_to_end_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("# Test Repo", encoding="utf-8")
            (root / "default_notice.py").write_text("default_notice_days = 5\n", encoding="utf-8")
            (root / "theme.css").write_text("body { color: black; }\n", encoding="utf-8")

            out = await _pipeline().ainvoke(
                {
                    "raw_input": {
                        "workspace_path": str(root),
                        "repo_url": "https://github.com/natchainsix/quicklend-nc",
                        "default_branch": "main",
                    }
                }
            )

        self.git.assert_not_called()
        report = out["findings_report"]
        result = out["deliverable_result"]
        self.assertEqual(report.source_type, "codebase")
        self.assertEqual(report.overall_verdict, "non_compliant")
        self.assertEqual(result.module_name, "codebase_review")
        self.assertEqual(result.status, "completed")
        self.assertFalse(result.metadata["cloned_remotely"])
        self.assertEqual(result.metadata["files_with_findings"], ["default_notice.py"])
        self.assertTrue(Path(result.artifacts[0]).is_file())
        self.assertTrue(Path(result.artifacts[1]).is_file())
        self.assertIn("`default_notice.py` L1-1", Path(result.artifacts[0]).read_text(encoding="utf-8"))

    async def test_remote_repo_url_invokes_shallow_git_clone(self) -> None:
        def fake_clone(*args: str) -> str:
            clone_dir = Path(args[-1])
            clone_dir.mkdir(parents=True, exist_ok=True)
            (clone_dir / "banner.py").write_text("hide_apr = True\n", encoding="utf-8")
            return ""

        self.git.side_effect = fake_clone
        out = await _pipeline().ainvoke(
            {"raw_input": {"repo_url": "https://github.com/natchainsix/quicklend-nc", "default_branch": "main"}}
        )

        self.git.assert_called_once()
        clone_args = self.git.call_args.args
        self.assertEqual(
            clone_args[:5],
            ("clone", "--depth", "1", "--branch", "main", "https://github.com/natchainsix/quicklend-nc.git")[:5],
        )
        self.assertEqual(clone_args[5], "https://github.com/natchainsix/quicklend-nc.git")
        self.assertTrue(out["deliverable_result"].metadata["cloned_remotely"])
        self.assertEqual(len(out["findings_report"].findings), 1)

    async def test_rejects_malicious_repo_url_and_branch(self) -> None:
        with self.assertRaises(ValueError):
            await _pipeline().ainvoke({"raw_input": {"repo_url": "https://evil.example/a/b; rm -rf /"}})
        with self.assertRaises(ValueError):
            await _pipeline().ainvoke(
                {"raw_input": {"repo_url": "https://github.com/natchainsix/quicklend-nc", "default_branch": "--upload-pack=evil"}}
            )


class MCPErrorAndEnvTests(unittest.TestCase):
    def test_parse_mcp_passages_ignores_tool_error_result(self) -> None:
        err = SimpleNamespace(
            isError=True,
            content=[SimpleNamespace(text="Error executing tool get_consumer_credit_act_document: no section 999")],
        )
        self.assertEqual(parse_mcp_passages(err, source="consumer_credit_act"), [])

    def test_build_default_core_subgraph_loads_env_before_reading_mcp_urls(self) -> None:
        from fca_compliance_agent import agent as agent_mod

        with mock.patch.object(agent_mod, "load_repo_env") as load_env:
            def _inject_env() -> None:
                os.environ["CCA_MCP_URL"] = "http://127.0.0.1:8001/mcp"

            load_env.side_effect = _inject_env
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("CCA_MCP_URL", None)
                os.environ.pop("FCA_MCP_URL", None)
                with mock.patch.object(agent_mod, "MCPConsumerCreditActAdapter") as mock_cca_mcp:
                    agent_mod.build_default_core_subgraph(
                        fca_port=MockFCAHandbookAdapter(),
                        analyzer_port=_FakeComplianceAnalyzer(),
                    )
                    load_env.assert_called_once()
                    mock_cca_mcp.assert_called_once()


if __name__ == "__main__":
    unittest.main()
