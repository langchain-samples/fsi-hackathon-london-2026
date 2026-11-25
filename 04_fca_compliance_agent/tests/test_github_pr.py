"""Tests for the real GitHub PR inbound/outbound behaviour adapters using `LocalMockGitHubClient`."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

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
from fca_compliance_agent.adapters.github_pr import build_github_pr_review_module, split_diff

DIFF = """diff --git a/src/contracts/withdrawal.py b/src/contracts/withdrawal.py
index 1111111..2222222 100644
--- a/src/contracts/withdrawal.py
+++ b/src/contracts/withdrawal.py
@@ -5,4 +5,5 @@ def withdrawal_window():
-withdrawal_days = 14
+withdrawal_days = 7  # shortened cooling-off window
@@ -20,2 +21,3 @@
+skip_affordability = True
diff --git a/uv.lock b/uv.lock
index 3333333..4444444 100644
--- a/uv.lock
+++ b/uv.lock
@@ -1,1 +1,1 @@
-a
+b
"""


def _pipeline(*, github_client: LocalMockGitHubClient, reports_dir: Path, checkpointer=None):
    core = build_core_analysis_subgraph(
        fca_port=MockFCAHandbookAdapter(),
        cca_port=MockConsumerCreditActAdapter(),
        analyzer_port=_FakeComplianceAnalyzer(),
    )
    return build_pipeline_for_module(
        build_github_pr_review_module(github_client=github_client, reports_dir=reports_dir),
        core_subgraph=core,
        checkpointer=checkpointer,
    )


class SplitDiffTest(unittest.TestCase):
    def test_one_unit_per_file_skips_lockfiles_and_anchors_lines(self) -> None:
        units = split_diff(DIFF)
        self.assertEqual([u.title for u in units], ["src/contracts/withdrawal.py"])
        self.assertEqual((units[0].location.start_line, units[0].location.end_line), (5, 23))


class GithubPrAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_synthetic_raw_diff_without_pr_identifier_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(output_dir=tmp_path / "mock_github", pr_diff=DIFF)
            with self.assertRaises(ValueError):
                await _pipeline(github_client=mock_gh, reports_dir=tmp_path / "reports").ainvoke(
                    {"raw_input": {"diff": DIFF, "title": "Shorten cooling-off"}}
                )
            self.assertEqual(mock_gh.viewed_prs, [])
            self.assertEqual(mock_gh.diffed_prs, [])
            self.assertEqual(mock_gh.posted_comments, [])

    async def test_pr_url_reads_pr_and_posts_comment_to_disk_and_stdout_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_github",
                pr_meta={"title": "T", "body": "B", "headRefOid": "abc"},
                pr_diff=DIFF,
            )
            stdout_buf = io.StringIO()
            with contextlib.redirect_stdout(stdout_buf):
                out = await _pipeline(github_client=mock_gh, reports_dir=tmp_path / "reports").ainvoke(
                    {"raw_input": {"pr_url": "https://github.com/acme/credit/pull/7"}}
                )
                mock_gh.print_summary()

            stdout_text = stdout_buf.getvalue()
            self.assertEqual(out["analysis_input"].target_id, "acme/credit#7")
            self.assertEqual(mock_gh.viewed_prs, [("acme/credit", 7)])
            self.assertEqual(mock_gh.diffed_prs, [("acme/credit", 7)])
            self.assertTrue(out["deliverable_result"].metadata["posted_to_github"])
            self.assertEqual(len(mock_gh.posted_comments), 1)
            comment = mock_gh.posted_comments[0]
            self.assertEqual(comment["comment_url"], "https://github.com/acme/credit/pull/7#issuecomment-1")
            self.assertIn(comment["comment_url"], out["deliverable_result"].artifacts)
            self.assertTrue(Path(comment["body_path"]).is_file())
            self.assertTrue(Path(comment["meta_path"]).is_file())
            self.assertIn("[MockGitHubAPI] Posted PR comment", stdout_text)
            self.assertIn("[MockGitHubAPI Summary]", stdout_text)

    async def test_bad_pr_url_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(output_dir=tmp_path / "mock_github")
            with self.assertRaises(ValueError):
                await _pipeline(github_client=mock_gh, reports_dir=tmp_path / "reports").ainvoke(
                    {"raw_input": {"pr_url": "https://evil.example/acme/credit/pull/7; rm -rf /"}}
                )

    async def test_post_to_github_false_saves_locally_without_commenting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_github",
                pr_meta={"title": "T", "body": "", "headRefOid": "a"},
                pr_diff=DIFF,
            )
            out = await _pipeline(github_client=mock_gh, reports_dir=tmp_path / "reports").ainvoke(
                {"raw_input": {"repo": "acme/credit", "pr_number": 7, "post_to_github": False}}
            )
            self.assertFalse(out["deliverable_result"].metadata["posted_to_github"])
            self.assertEqual(mock_gh.viewed_prs, [("acme/credit", 7)])
            self.assertEqual(mock_gh.diffed_prs, [("acme/credit", 7)])
            self.assertEqual(mock_gh.posted_comments, [])

    async def test_post_after_human_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_github",
                pr_meta={"title": "T", "body": "", "headRefOid": "a"},
                pr_diff=DIFF,
            )
            graph = _pipeline(
                github_client=mock_gh,
                reports_dir=tmp_path / "reports",
                checkpointer=InMemorySaver(),
            )
            cfg = {"configurable": {"thread_id": "t1"}}
            raw = {"repo": "acme/credit", "pr_number": 7}
            await graph.ainvoke({"raw_input": raw, "require_human_approval": True}, cfg)
            self.assertEqual(mock_gh.posted_comments, [])

            out = await graph.ainvoke(Command(resume={"action": "approve"}), cfg)
            self.assertTrue(out["deliverable_result"].metadata["posted_to_github"])
            self.assertEqual(len(mock_gh.posted_comments), 1)
            self.assertTrue(Path(mock_gh.posted_comments[0]["body_path"]).is_file())

    async def test_hitl_reject_aborts_without_posting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mock_gh = LocalMockGitHubClient(
                output_dir=tmp_path / "mock_github",
                pr_meta={"title": "T", "body": "", "headRefOid": "a"},
                pr_diff=DIFF,
            )
            graph = _pipeline(
                github_client=mock_gh,
                reports_dir=tmp_path / "reports",
                checkpointer=InMemorySaver(),
            )
            cfg = {"configurable": {"thread_id": "t-reject"}}
            raw = {"repo": "acme/credit", "pr_number": 7}
            await graph.ainvoke({"raw_input": raw, "require_human_approval": True}, cfg)
            out = await graph.ainvoke(Command(resume={"action": "reject"}), cfg)
            self.assertEqual(out["deliverable_result"].status, "aborted_by_reviewer")
            self.assertEqual(mock_gh.posted_comments, [])


if __name__ == "__main__":
    unittest.main()
