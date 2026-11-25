"""Tests for the Codebase Review module, Code Remediator outbound agent, and GitHub API adapters.

Exercises the REAL inbound/outbound behaviour adapters while injecting `LocalMockGitHubClient`
(mock API adapter writing fake GitHub repos/branches/PRs to local disk and reporting on stdout).
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fakes import (
    LocalMockGitHubClient,
    MockConsumerCreditActAdapter,
    MockFCAHandbookAdapter,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from test_pipeline import _FakeComplianceAnalyzer

from fca_compliance_agent import (
    build_core_analysis_subgraph,
    build_pipeline_for_module,
)
from fca_compliance_agent.adapters.github_codebase import (
    FileRemediationResult,
    build_github_codebase_review_module,
    collect_codebase_units,
)
from fca_compliance_agent.adapters.outbound.github_client import GitHubCLIAdapter
from fca_compliance_agent.adapters.regulatory.mcp import parse_mcp_passages


class _FakeCodeRemediator:
    """Deterministic test double for `CodeRemediator`."""

    def __init__(self, *, updated_content: str | None = None, can_fix: bool = True) -> None:
        self._updated_content = updated_content
        self._can_fix = can_fix
        self.calls: list[dict[str, object]] = []

    async def remediate_file(
        self,
        *,
        file_path: str,
        original_content: str,
        findings,
        repo_summary: str = "",
    ) -> FileRemediationResult:
        self.calls.append({"file_path": file_path, "findings": len(findings), "repo_summary": repo_summary})
        if not self._can_fix:
            return FileRemediationResult(can_fix=False, updated_content="", change_summary="Cannot auto-fix.")
        content = self._updated_content
        if content is None:
            content = original_content.replace("hide_apr = True", "hide_apr = False  # show representative APR")
            if content == original_content:
                content = original_content + "\n# compliance fix applied\n"
        return FileRemediationResult(
            can_fix=True,
            updated_content=content,
            change_summary=f"Remediated {len(findings)} compliance finding(s) in {file_path}.",
        )


def _pipeline(*, remediator=None, github_client=None, reports_dir: Path | None = None, checkpointer=None):
    core = build_core_analysis_subgraph(
        fca_port=MockFCAHandbookAdapter(),
        cca_port=MockConsumerCreditActAdapter(),
        analyzer_port=_FakeComplianceAnalyzer(),
    )
    return build_pipeline_for_module(
        build_github_codebase_review_module(
            remediator=remediator,
            github_client=github_client,
            reports_dir=reports_dir,
        ),
        core_subgraph=core,
        checkpointer=checkpointer,
    )


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
    async def test_local_workspace_runs_end_to_end_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "workspace"
            root.mkdir()
            (root / "README.md").write_text("# Test Repo", encoding="utf-8")
            (root / "default_notice.py").write_text("default_notice_days = 5\n", encoding="utf-8")
            (root / "theme.css").write_text("body { color: black; }\n", encoding="utf-8")

            mock_gh = LocalMockGitHubClient(output_dir=tmp_path / "mock_github")
            remediator = _FakeCodeRemediator()
            out = await _pipeline(
                remediator=remediator,
                github_client=mock_gh,
                reports_dir=tmp_path / "reports",
            ).ainvoke(
                {
                    "raw_input": {
                        "workspace_path": str(root),
                        "repo_url": "https://github.com/natchainsix/quicklend-nc",
                        "default_branch": "main",
                    }
                }
            )

            self.assertEqual(mock_gh.cloned, [])
            self.assertEqual(mock_gh.created_prs, [])
            report = out["findings_report"]
            result = out["deliverable_result"]
            self.assertEqual(report.source_type, "codebase")
            self.assertEqual(report.overall_verdict, "non_compliant")
            self.assertEqual(result.module_name, "codebase_review")
            self.assertEqual(result.status, "completed")
            self.assertFalse(result.metadata["cloned_remotely"])
            self.assertFalse(result.metadata["pr_created"])
            self.assertEqual(result.metadata["files_with_findings"], ["default_notice.py"])
            self.assertTrue(Path(result.artifacts[0]).is_file())
            self.assertTrue(Path(result.artifacts[1]).is_file())
            self.assertIn("`default_notice.py` L1-1", Path(result.artifacts[0]).read_text(encoding="utf-8"))

    async def test_remote_repo_opens_remediation_pr_to_disk_and_reports_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_github",
                repo_files={"banner.py": "hide_apr = True\n"},
            )
            remediator = _FakeCodeRemediator()
            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                out = await _pipeline(
                    remediator=remediator,
                    github_client=mock_gh,
                    reports_dir=tmp_path / "reports",
                ).ainvoke(
                    {"raw_input": {"repo_url": "https://github.com/natchainsix/quicklend-nc", "default_branch": "main"}}
                )
                mock_gh.print_summary()

            stdout_text = stdout_buf.getvalue()
            result = out["deliverable_result"]
            self.assertEqual(result.status, "completed")
            self.assertTrue(result.metadata["cloned_remotely"])
            self.assertTrue(result.metadata["pr_created"])
            self.assertEqual(result.metadata["pr_url"], "https://github.com/natchainsix/quicklend-nc/pull/1")
            self.assertEqual(result.metadata["edited_files"], ["banner.py"])
            self.assertIn("https://github.com/natchainsix/quicklend-nc/pull/1", result.artifacts)
            self.assertEqual(len(mock_gh.cloned), 2)  # inbound scan + outbound remediation clone
            self.assertEqual(len(mock_gh.commits), 1)
            commit = mock_gh.commits[0]
            self.assertTrue(commit["branch"].startswith("compliance/fca-cca-rep-"))
            self.assertEqual(commit["files"], ["banner.py"])
            self.assertIn("hide_apr = False", commit["written_files"]["banner.py"])
            self.assertTrue(Path(commit["meta_path"]).is_file())

            self.assertEqual(len(mock_gh.created_prs), 1)
            pr = mock_gh.created_prs[0]
            self.assertEqual(pr["repo_slug"], "natchainsix/quicklend-nc")
            self.assertEqual(pr["base_branch"], "main")
            self.assertFalse(pr["draft"])
            self.assertTrue(Path(pr["body_path"]).is_file())
            self.assertTrue(Path(pr["meta_path"]).is_file())
            self.assertIn("[MockGitHubAPI] Opened PR", stdout_text)
            self.assertIn("[MockGitHubAPI Summary]", stdout_text)

    async def test_create_pr_false_saves_locally_without_opening_pr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_github",
                repo_files={"banner.py": "hide_apr = True\n"},
            )
            remediator = _FakeCodeRemediator()
            out = await _pipeline(
                remediator=remediator,
                github_client=mock_gh,
                reports_dir=tmp_path / "reports",
            ).ainvoke(
                {
                    "raw_input": {
                        "repo_url": "https://github.com/natchainsix/quicklend-nc",
                        "default_branch": "main",
                        "create_pr": False,
                    }
                }
            )

            result = out["deliverable_result"]
            self.assertEqual(result.status, "completed")
            self.assertFalse(result.metadata["pr_created"])
            self.assertEqual(len(mock_gh.cloned), 1)  # inbound only
            self.assertEqual(mock_gh.created_prs, [])
            self.assertEqual(remediator.calls, [])

    async def test_zero_findings_skips_pr_creation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_github",
                repo_files={"clean.py": "apr = 19.9\n"},
            )
            remediator = _FakeCodeRemediator()
            out = await _pipeline(
                remediator=remediator,
                github_client=mock_gh,
                reports_dir=tmp_path / "reports",
            ).ainvoke(
                {"raw_input": {"repo_url": "https://github.com/natchainsix/quicklend-nc", "default_branch": "main"}}
            )

            result = out["deliverable_result"]
            self.assertEqual(result.status, "completed")
            self.assertFalse(result.metadata["pr_created"])
            self.assertEqual(mock_gh.created_prs, [])
            self.assertEqual(remediator.calls, [])

    async def test_invalid_python_remediation_is_rejected_and_not_committed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_github",
                repo_files={"banner.py": "hide_apr = True\n"},
            )
            remediator = _FakeCodeRemediator(updated_content="def broken(:\n    pass\n")
            out = await _pipeline(
                remediator=remediator,
                github_client=mock_gh,
                reports_dir=tmp_path / "reports",
            ).ainvoke(
                {"raw_input": {"repo_url": "https://github.com/natchainsix/quicklend-nc", "default_branch": "main"}}
            )

            result = out["deliverable_result"]
            self.assertEqual(result.status, "completed")
            self.assertFalse(result.metadata["pr_created"])
            self.assertEqual(mock_gh.commits, [])
            self.assertEqual(mock_gh.created_prs, [])
            self.assertIn("Invalid Python syntax", result.metadata["skipped_remediations"]["banner.py"])

    async def test_hitl_approval_gate_supports_approve_and_reject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw = {"repo_url": "https://github.com/natchainsix/quicklend-nc", "default_branch": "main"}

            gh_reject = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_reject",
                repo_files={"banner.py": "hide_apr = True\n"},
            )
            graph_reject = _pipeline(
                remediator=_FakeCodeRemediator(),
                github_client=gh_reject,
                reports_dir=tmp_path / "reports_reject",
                checkpointer=InMemorySaver(),
            )
            cfg = {"configurable": {"thread_id": "cb-reject"}}
            await graph_reject.ainvoke({"raw_input": raw, "require_human_approval": True}, cfg)
            out_reject = await graph_reject.ainvoke(Command(resume={"action": "reject"}), cfg)
            self.assertEqual(out_reject["deliverable_result"].status, "aborted_by_reviewer")
            self.assertEqual(gh_reject.created_prs, [])

            gh_approve = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_approve",
                repo_files={"banner.py": "hide_apr = True\n"},
            )
            graph_approve = _pipeline(
                remediator=_FakeCodeRemediator(),
                github_client=gh_approve,
                reports_dir=tmp_path / "reports_approve",
                checkpointer=InMemorySaver(),
            )
            cfg2 = {"configurable": {"thread_id": "cb-approve"}}
            await graph_approve.ainvoke({"raw_input": raw, "require_human_approval": True}, cfg2)
            out_approve = await graph_approve.ainvoke(Command(resume={"action": "approve"}), cfg2)
            self.assertTrue(out_approve["deliverable_result"].metadata["pr_created"])
            self.assertEqual(len(gh_approve.created_prs), 1)
            self.assertTrue(Path(gh_approve.created_prs[0]["body_path"]).is_file())

    async def test_pr_creation_failure_keeps_local_report_and_marks_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_github",
                repo_files={"banner.py": "hide_apr = True\n"},
                fail_on_create="gh pr create failed: permission denied",
            )
            out = await _pipeline(
                remediator=_FakeCodeRemediator(),
                github_client=mock_gh,
                reports_dir=tmp_path / "reports",
            ).ainvoke(
                {"raw_input": {"repo_url": "https://github.com/natchainsix/quicklend-nc", "default_branch": "main"}}
            )

            result = out["deliverable_result"]
            self.assertEqual(result.status, "failed")
            self.assertFalse(result.metadata["pr_created"])
            self.assertIn("permission denied", result.metadata["pr_error"])
            self.assertTrue(Path(result.artifacts[0]).is_file())
            self.assertTrue(Path(result.artifacts[1]).is_file())

    async def test_rejects_malicious_repo_url_and_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mock_gh = LocalMockGitHubClient(output_dir=Path(tmp) / "mock_github")
            with self.assertRaises(ValueError):
                await _pipeline(github_client=mock_gh).ainvoke(
                    {"raw_input": {"repo_url": "https://evil.example/a/b; rm -rf /"}}
                )
            with self.assertRaises(ValueError):
                await _pipeline(github_client=mock_gh).ainvoke(
                    {"raw_input": {"repo_url": "https://github.com/natchainsix/quicklend-nc", "default_branch": "--upload-pack=evil"}}
                )


class GitHubCLIAdapterTests(unittest.TestCase):
    def test_commit_and_push_sets_local_identity_and_falls_back_to_gh_credentials(self) -> None:
        calls: list[tuple[tuple[str, ...], Path | None]] = []

        def fake_git(*args: str, cwd: Path | None = None) -> str:
            calls.append((args, cwd))
            if args[:2] == ("config", "--get"):
                return ""
            if args[:3] == ("push", "-u", "origin"):
                raise RuntimeError("git push failed: could not read Username for 'https://github.com': terminal prompts disabled")
            return ""

        client = GitHubCLIAdapter(git_runner=fake_git, gh_runner=lambda *a, **k: "")
        with mock.patch("fca_compliance_agent.adapters.outbound.github_client.shutil.which", return_value="/usr/bin/gh"):
            client.commit_and_push(Path("/tmp/repo"), "compliance/fix", ["app.py"], "fix: compliance")

        arg_list = [c[0] for c in calls]
        self.assertIn(("config", "user.name", "FCA Compliance Agent"), arg_list)
        self.assertIn(("config", "user.email", "fca-compliance-agent@users.noreply.github.com"), arg_list)
        self.assertIn(("add", "--", "app.py"), arg_list)
        self.assertIn(("commit", "-m", "fix: compliance"), arg_list)
        self.assertIn(
            ("-c", "credential.helper=!gh auth git-credential", "push", "-u", "origin", "compliance/fix"),
            arg_list,
        )

    def test_create_pr_passes_draft_flag_when_requested(self) -> None:
        gh_calls: list[tuple[str, ...]] = []

        def fake_gh(*args: str, cwd: Path | None = None) -> str:
            gh_calls.append(args)
            return "https://github.com/owner/repo/pull/99\n"

        client = GitHubCLIAdapter(git_runner=lambda *a, **k: "", gh_runner=fake_gh)
        url = client.create_pr(
            repo_slug="owner/repo",
            base_branch="main",
            head_branch="compliance/fix",
            title="Fix compliance",
            body_file=Path("/tmp/body.md"),
            draft=True,
        )
        self.assertEqual(url, "https://github.com/owner/repo/pull/99")
        self.assertIn("--draft", gh_calls[0])


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

    def test_build_default_core_subgraph_always_wires_real_mcp_adapters(self) -> None:
        from fca_compliance_agent import agent as agent_mod
        from fca_compliance_agent.mcp_server.bootstrap import DEFAULT_FCA_MCP_URL

        with (
            mock.patch.object(agent_mod, "load_repo_env"),
            mock.patch.dict(os.environ, {}, clear=False),
            mock.patch.object(agent_mod, "MCPFCAHandbookAdapter") as mock_fca_mcp,
            mock.patch.object(agent_mod, "MCPConsumerCreditActAdapter") as mock_cca_mcp,
        ):
            os.environ.pop("CCA_MCP_URL", None)
            os.environ.pop("FCA_MCP_URL", None)
            agent_mod.build_default_core_subgraph(
                analyzer_port=_FakeComplianceAnalyzer(),
            )
            mock_fca_mcp.assert_called_once()
            fca_cfg = mock_fca_mcp.call_args.kwargs["server_config"]
            self.assertEqual(fca_cfg.transport, "streamable_http")
            self.assertEqual(fca_cfg.url, DEFAULT_FCA_MCP_URL)

            mock_cca_mcp.assert_called_once()
            cca_cfg = mock_cca_mcp.call_args.kwargs["server_config"]
            self.assertEqual(cca_cfg.transport, "stdio")


if __name__ == "__main__":
    unittest.main()
