"""Tests for the real GitHub PR inbound/outbound adapters (no network: `gh` is patched)."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from fca_compliance_agent import (
    MockComplianceAnalyzer,
    MockConsumerCreditActAdapter,
    MockFCAHandbookAdapter,
    build_core_analysis_subgraph,
    build_pipeline_for_module,
)
from fca_compliance_agent.adapters import github_pr
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


def _pipeline(checkpointer=None):
    core = build_core_analysis_subgraph(
        fca_port=MockFCAHandbookAdapter(),
        cca_port=MockConsumerCreditActAdapter(),
        analyzer_port=MockComplianceAnalyzer(),
    )
    return build_pipeline_for_module(build_github_pr_review_module(), core_subgraph=core, checkpointer=checkpointer)


class SplitDiffTest(unittest.TestCase):
    def test_one_unit_per_file_skips_lockfiles_and_anchors_lines(self) -> None:
        units = split_diff(DIFF)
        self.assertEqual([u.title for u in units], ["src/contracts/withdrawal.py"])
        self.assertEqual((units[0].location.start_line, units[0].location.end_line), (5, 23))


class GithubPrAdapterTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.gh = mock.patch.object(github_pr, "_gh").start()
        self.addCleanup(mock.patch.stopall)

    async def test_raw_diff_writes_local_report_and_never_calls_github(self) -> None:
        out = await _pipeline().ainvoke({"raw_input": {"diff": DIFF, "title": "Shorten cooling-off"}})
        result = out["deliverable_result"]
        self.assertEqual(result.status, "completed")
        self.assertFalse(result.metadata["posted_to_github"])
        self.assertTrue(result.artifacts[0].endswith(".md"))
        self.gh.assert_not_called()

    async def test_pr_url_reads_pr_with_gh(self) -> None:
        self.gh.side_effect = [json.dumps({"title": "T", "body": "B", "url": "u", "headRefOid": "abc"}), DIFF]
        out = await _pipeline().ainvoke({"raw_input": {"pr_url": "https://github.com/acme/credit/pull/7"}})
        self.assertEqual(out["analysis_input"].target_id, "acme/credit#7")
        self.assertEqual(self.gh.call_args_list[1].args, ("pr", "diff", "7", "--repo", "acme/credit"))

    async def test_bad_pr_url_rejected(self) -> None:
        with self.assertRaises(ValueError):
            await _pipeline().ainvoke({"raw_input": {"pr_url": "https://evil.example/acme/credit/pull/7; rm -rf /"}})

    async def test_post_requested_without_human_gate_is_refused(self) -> None:
        self.gh.side_effect = [json.dumps({"title": "T", "body": "", "url": "u", "headRefOid": "a"}), DIFF]
        out = await _pipeline().ainvoke(
            {"raw_input": {"repo": "acme/credit", "pr_number": 7, "post_to_github": True}}
        )
        self.assertFalse(out["deliverable_result"].metadata["posted_to_github"])
        self.assertEqual(self.gh.call_count, 2)  # view + diff only, no comment

    async def test_post_after_human_approval(self) -> None:
        self.gh.side_effect = [
            json.dumps({"title": "T", "body": "", "url": "u", "headRefOid": "a"}),
            DIFF,
            "https://github.com/acme/credit/pull/7#issuecomment-1\n",
        ]
        graph = _pipeline(checkpointer=InMemorySaver())
        cfg = {"configurable": {"thread_id": "t1"}}
        raw = {"repo": "acme/credit", "pr_number": 7, "post_to_github": True}
        await graph.ainvoke({"raw_input": raw, "require_human_approval": True}, cfg)
        out = await graph.ainvoke(Command(resume={"action": "approve"}), cfg)
        self.assertTrue(out["deliverable_result"].metadata["posted_to_github"])
        self.assertEqual(self.gh.call_args_list[2].args[:3], ("pr", "comment", "7"))


if __name__ == "__main__":
    unittest.main()
