"""Tests for the Compliance Agent HTTP API adapter and `make run` setup orchestrator.

Follows AGENTS.md testing rules: exercises real behaviour adapters and FastAPI routes
while injecting test-only fakes at the external API boundary.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from fakes import (
    LocalMockGitHubClient,
    MockConsumerCreditActAdapter,
    MockFCAHandbookAdapter,
)
from test_pipeline import _FakeComplianceAnalyzer

from fca_compliance_agent import build_core_analysis_subgraph
from fca_compliance_agent.adapters.http_api import create_app

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _load_setup_module():
    spec = importlib.util.spec_from_file_location("setup_and_run", SCRIPTS_DIR / "setup_and_run.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _build_test_core():
    return build_core_analysis_subgraph(
        fca_port=MockFCAHandbookAdapter(),
        cca_port=MockConsumerCreditActAdapter(),
        analyzer_port=_FakeComplianceAnalyzer(),
    )


class TestComplianceHttpApi(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.reports_dir = self.tmp_path / "reports"
        self.mock_gh = LocalMockGitHubClient(
            output_dir=self.tmp_path / "mock_github",
            repo_files={
                "lending/withdrawal.py": "withdrawal_days = 7\n",
                "lending/affordability.py": "skip_affordability = True\n",
                "static/style.css": "body { margin: 0; }\n",
            },
            pr_meta={
                "title": "PR #12: Update withdrawal window",
                "body": "Shorten withdrawal window.",
                "headRefOid": "abc123def",
            },
            pr_diff=(
                "diff --git a/lending/withdrawal.py b/lending/withdrawal.py\n"
                "--- a/lending/withdrawal.py\n"
                "+++ b/lending/withdrawal.py\n"
                "@@ -1,1 +1,1 @@\n"
                "-withdrawal_days = 14\n"
                "+withdrawal_days = 7\n"
            ),
            org_repos=[
                {
                    "full_name": "natchainsix/safelend-co",
                    "url": "https://github.com/natchainsix/safelend-co",
                    "default_branch": "main",
                    "description": "Demo lending codebase",
                    "private": False,
                    "pushed_at": "2026-11-25T12:00:00Z",
                }
            ],
            repo_prs={
                "natchainsix/safelend-co": [
                    {
                        "number": 12,
                        "title": "Update withdrawal window",
                        "url": "https://github.com/natchainsix/safelend-co/pull/12",
                        "state": "OPEN",
                        "head_ref": "feat/withdrawal",
                        "base_ref": "main",
                        "author": "alice",
                        "updated_at": "2026-11-25T12:30:00Z",
                    }
                ]
            },
            report_to_stdout=False,
        )
        self.app = create_app(
            core_subgraph=_build_test_core(),
            github_client=self.mock_gh,
            reports_dir=self.reports_dir,
            default_org="natchainsix",
        )
        self._client_cm = TestClient(self.app)
        self.client = self._client_cm.__enter__()

    def tearDown(self) -> None:
        self._client_cm.__exit__(None, None, None)
        self._tmp.cleanup()

    def test_health_and_targets_discovery(self) -> None:
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        hdata = health.json()
        self.assertEqual(hdata["status"], "ok")
        self.assertEqual(hdata["default_org"], "natchainsix")

        targets = self.client.get("/api/targets")
        self.assertEqual(targets.status_code, 200)
        tdata = targets.json()
        self.assertEqual(tdata["org"], "natchainsix")
        self.assertEqual(len(tdata["repositories"]), 1)
        repo = tdata["repositories"][0]
        self.assertEqual(repo["full_name"], "natchainsix/safelend-co")
        self.assertEqual(len(repo["pull_requests"]), 1)
        self.assertEqual(repo["pull_requests"][0]["number"], 12)

        pulls = self.client.get("/api/targets/natchainsix/safelend-co/pulls")
        self.assertEqual(pulls.status_code, 200)
        self.assertEqual(pulls.json()["pull_requests"][0]["title"], "Update withdrawal window")

    def test_review_direct_and_langserve_match_regulens_contract_and_persist_findings(self) -> None:
        payload = {
            "repository": "natchainsix/safelend-co",
            "commit": "abcdef123456",
            "files": [
                {"path": "withdrawal.py", "content": "# header\nwithdrawal_days = 7\n"},
                {"path": "affordability.py", "content": "skip_affordability = True\n"},
            ],
            "instructions": "Review FCA and CCA compliance.",
            "specialty": "fca_compliance",
        }
        resp = self.client.post("/review", json=payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["overall_verdict"], "non_compliant")
        self.assertEqual(len(body["findings"]), 2)

        files_lines = {f["path"]: f["content"].splitlines() for f in payload["files"]}
        for finding in body["findings"]:
            self.assertIn(finding["status"], {"NON_COMPLIANT", "COMPLIANT_WITHIN_SCOPE", "NEEDS_CONTEXT"})
            self.assertIn(finding["severity"], {"CRITICAL", "HIGH", "MEDIUM", "LOW"})
            self.assertIn(finding["file"], files_lines)
            self.assertIsInstance(finding["line_start"], int)
            self.assertGreaterEqual(finding["line_start"], 1)
            self.assertLessEqual(finding["line_start"], len(files_lines[finding["file"]]))
            for field in ("title", "regulation", "evidence", "remediation"):
                self.assertTrue(finding[field].strip())
                self.assertLessEqual(len(finding[field]), 4000)

        # LangServe envelope endpoint
        ls_resp = self.client.post("/review/invoke", json={"input": payload})
        self.assertEqual(ls_resp.status_code, 200)
        self.assertIn("output", ls_resp.json())
        self.assertEqual(len(ls_resp.json()["output"]["findings"]), 2)

        # Findings and report retrieval endpoints expose persisted reports
        findings_resp = self.client.get("/api/findings")
        self.assertEqual(findings_resp.status_code, 200)
        fdata = findings_resp.json()
        self.assertGreaterEqual(fdata["total_reports"], 2)
        self.assertGreaterEqual(fdata["total_findings"], 4)

        report_id = body["report_id"]
        report_resp = self.client.get(f"/api/reports/{report_id}")
        self.assertEqual(report_resp.status_code, 200)
        self.assertEqual(report_resp.json()["report"]["report_id"], report_id)
        self.assertIn("FCA", report_resp.json()["markdown"])

    def test_async_codebase_and_pr_runs(self) -> None:
        start = self.client.post(
            "/api/runs",
            json={
                "mode": "codebase_review",
                "repo_url": "https://github.com/natchainsix/safelend-co",
                "branch": "main",
                "create_pr": False,
            },
        )
        self.assertEqual(start.status_code, 202)
        run_id = start.json()["run_id"]

        final: dict[str, Any] = {}
        for _ in range(50):
            polled = self.client.get(f"/api/runs/{run_id}")
            self.assertEqual(polled.status_code, 200)
            final = polled.json()
            if final["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)

        self.assertEqual(final["status"], "completed", msg=str(final))
        self.assertEqual(final["findings_report"]["overall_verdict"], "non_compliant")
        self.assertEqual(len(final["findings_report"]["findings"]), 2)
        self.assertEqual(self.mock_gh.created_prs, [])  # create_pr=False stays read-only

        pr_start = self.client.post(
            "/api/runs",
            json={
                "mode": "pr_review",
                "pr_url": "https://github.com/natchainsix/safelend-co/pull/12",
                "post_to_github": False,
            },
        )
        self.assertEqual(pr_start.status_code, 202)
        pr_run_id = pr_start.json()["run_id"]
        pr_final: dict[str, Any] = {}
        for _ in range(50):
            polled = self.client.get(f"/api/runs/{pr_run_id}")
            pr_final = polled.json()
            if pr_final["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)

        self.assertEqual(pr_final["status"], "completed", msg=str(pr_final))
        self.assertEqual(len(pr_final["findings_report"]["findings"]), 1)
        self.assertEqual(self.mock_gh.posted_comments, [])


class TestSetupOrchestrator(unittest.TestCase):
    def setUp(self) -> None:
        self.mod = _load_setup_module()

    def test_ensure_fca_api_token_prompts_and_persists_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_fca = Path(tmp) / ".env.fca"
            prompts: list[str] = []

            def fake_input(prompt: str) -> str:
                prompts.append(prompt)
                return "fca-secret-token-123"

            token = self.mod.ensure_fca_api_token(env_fca, input_fn=fake_input, interactive=True)
            self.assertEqual(token, "fca-secret-token-123")
            self.assertEqual(len(prompts), 1)
            self.assertTrue(env_fca.is_file())
            self.assertIn("FCA_HANDBOOK_API_TOKEN=fca-secret-token-123", env_fca.read_text(encoding="utf-8"))
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(env_fca.stat().st_mode), 0o600)

            # Second call reuses persisted token without prompting again
            prompts.clear()
            token_again = self.mod.ensure_fca_api_token(env_fca, input_fn=fake_input, interactive=True)
            self.assertEqual(token_again, "fca-secret-token-123")
            self.assertEqual(prompts, [])

    def test_ensure_fca_api_token_raises_clearly_when_non_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_fca = Path(tmp) / ".env.fca"
            with self.assertRaisesRegex(RuntimeError, r"FCA_HANDBOOK_API_TOKEN"):
                self.mod.ensure_fca_api_token(env_fca, interactive=False)

    def test_qdrant_alias_and_snapshot_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rag_dir = Path(tmp)
            qdrant_dir = rag_dir / ".qdrant"
            qdrant_dir.mkdir()
            (qdrant_dir / "meta.json").write_text(json.dumps({"collections": {}, "aliases": {}}), encoding="utf-8")
            self.assertIsNone(self.mod.get_qdrant_alias_target(rag_dir, "fca_handbook"))

            (qdrant_dir / "meta.json").write_text(
                json.dumps(
                    {
                        "collections": {"fca_handbook__20261125_abc": {}},
                        "aliases": {"fca_handbook": "fca_handbook__20261125_abc"},
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                self.mod.get_qdrant_alias_target(rag_dir, "fca_handbook"),
                "fca_handbook__20261125_abc",
            )

            # Incomplete snapshot (raw only) is ignored; complete snapshot is selected
            partial = rag_dir / "data" / "snapshots" / "2026-11-25"
            (partial / "raw").mkdir(parents=True)
            self.assertIsNone(self.mod.find_latest_complete_snapshot(rag_dir))

            complete = rag_dir / "data" / "snapshots" / "2026-11-24"
            complete.mkdir(parents=True)
            (complete / "provisions.jsonl").write_text("{}\n", encoding="utf-8")
            (complete / "manifest.json").write_text("{}\n", encoding="utf-8")
            self.assertEqual(self.mod.find_latest_complete_snapshot(rag_dir), complete)

    def test_configure_agent_ui_env_prompts_when_missing_and_stays_minimal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ui_dir = Path(tmp)
            answers = iter(["http://127.0.0.1:8002", "240"])
            prompts: list[str] = []

            def fake_input(prompt: str) -> str:
                prompts.append(prompt)
                return next(answers)

            env_path = self.mod.configure_agent_ui_env(
                ui_dir,
                api_url="http://127.0.0.1:8002",
                input_fn=fake_input,
                interactive=True,
            )
            text = env_path.read_text(encoding="utf-8")
            self.assertEqual(len(prompts), 2)
            self.assertIn("MODE=backend", text)
            self.assertIn("COMPLIANCE_API_URL=http://127.0.0.1:8002", text)
            self.assertIn("AGENT_TIMEOUT_SECONDS=240", text)
            self.assertNotIn("GITHUB_TOKEN", text)

            # Second call does not re-prompt once persisted
            prompts.clear()
            self.mod.configure_agent_ui_env(
                ui_dir,
                api_url="http://127.0.0.1:8002",
                input_fn=fake_input,
                interactive=True,
            )
            self.assertEqual(prompts, [])

    def test_ensure_repo_env_prompts_for_missing_backend_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            example = Path(tmp) / ".env.example"
            example.write_text(
                "ATTENDEE_NAME=<replace-with-your-name>\n"
                "MODEL_NAME=openai:gpt-6-luna\n"
                "LANGSMITH_API_KEY=<replace-with-your-api-key>\n"
                "LANGSMITH_WORKSPACE_ID=<replace-with-your-workspace-id>\n",
                encoding="utf-8",
            )
            answers = iter(["james", "lsv2_secret_key", "ws-123", "natchainsix"])
            result = self.mod.ensure_repo_env(
                env_path,
                example,
                input_fn=lambda _p: next(answers),
                interactive=True,
            )
            text = env_path.read_text(encoding="utf-8")
            self.assertEqual(result["LANGSMITH_API_KEY"], "lsv2_secret_key")
            self.assertIn("ATTENDEE_NAME=james", text)
            self.assertIn("LANGSMITH_API_KEY=lsv2_secret_key", text)
            self.assertIn("LANGSMITH_WORKSPACE_ID=ws-123", text)
            self.assertIn("GITHUB_ORG=natchainsix", text)


if __name__ == "__main__":
    unittest.main()
