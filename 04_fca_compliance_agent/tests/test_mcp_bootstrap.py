"""Tests for background MCP server bootstrapping and runner CLI options."""

from __future__ import annotations

import asyncio
import importlib.util
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fca_compliance_agent.mcp_server.bootstrap import (
    DEFAULT_FCA_MCP_URL,
    DEFAULT_MCP_HOST,
    is_mcp_installed,
    managed_cca_mcp_server,
    managed_fca_mcp_server,
    managed_mcp_server,
    pick_free_port,
    resolve_cca_mcp_mode,
    resolve_fca_mcp_mode,
    wait_for_port,
)

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"


def _load_runner_module():
    spec = importlib.util.spec_from_file_location("run_draft_test", SCRIPTS_DIR / "run_draft_test.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _can_connect_loopback() -> bool:
    """Return True if the runtime environment allows TCP connect to 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False


class ResolveMcpModeTests(unittest.TestCase):
    def test_cca_defaults_to_auto_bootstrap_when_nothing_configured(self) -> None:
        url, should_bootstrap = resolve_cca_mcp_mode(cca_mcp_url=None, environ={})
        self.assertIsNone(url)
        self.assertTrue(should_bootstrap)

    def test_cca_explicit_url_disables_bootstrap(self) -> None:
        url, should_bootstrap = resolve_cca_mcp_mode(
            cca_mcp_url="http://127.0.0.1:9999/mcp",
            environ={},
        )
        self.assertEqual(url, "http://127.0.0.1:9999/mcp")
        self.assertFalse(should_bootstrap)

    def test_cca_existing_env_url_disables_bootstrap(self) -> None:
        url, should_bootstrap = resolve_cca_mcp_mode(
            cca_mcp_url=None,
            environ={"CCA_MCP_URL": "http://127.0.0.1:8001/mcp"},
        )
        self.assertEqual(url, "http://127.0.0.1:8001/mcp")
        self.assertFalse(should_bootstrap)

    def test_fca_defaults_to_auto_bootstrap_when_not_configured_or_listening(self) -> None:
        url, should_bootstrap = resolve_fca_mcp_mode(
            fca_mcp_url=None,
            environ={},
            check_default_port=lambda _h, _p: False,
        )
        self.assertIsNone(url)
        self.assertTrue(should_bootstrap)

    def test_fca_reuses_already_running_default_handbook_rag_server(self) -> None:
        url, should_bootstrap = resolve_fca_mcp_mode(
            fca_mcp_url=None,
            environ={},
            check_default_port=lambda _h, _p: True,
        )
        self.assertEqual(url, DEFAULT_FCA_MCP_URL)
        self.assertFalse(should_bootstrap)

    def test_fca_explicit_url_or_env_disables_bootstrap(self) -> None:
        url, should_bootstrap = resolve_fca_mcp_mode(
            fca_mcp_url="http://127.0.0.1:9876/mcp",
            environ={},
            check_default_port=lambda _h, _p: False,
        )
        self.assertEqual(url, "http://127.0.0.1:9876/mcp")
        self.assertFalse(should_bootstrap)

        env_url, env_bootstrap = resolve_fca_mcp_mode(
            fca_mcp_url=None,
            environ={"FCA_MCP_URL": "http://127.0.0.1:8765/mcp"},
            check_default_port=lambda _h, _p: False,
        )
        self.assertEqual(env_url, "http://127.0.0.1:8765/mcp")
        self.assertFalse(env_bootstrap)


class PickFreePortTests(unittest.TestCase):
    def test_uses_preferred_port_when_available(self) -> None:
        free_port = pick_free_port(DEFAULT_MCP_HOST, preferred_port=None)
        chosen = pick_free_port(DEFAULT_MCP_HOST, preferred_port=free_port)
        self.assertEqual(chosen, free_port)

    def test_falls_back_when_preferred_port_is_busy(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind((DEFAULT_MCP_HOST, 0))
            occupied.listen(1)
            busy_port = occupied.getsockname()[1]

            chosen = pick_free_port(DEFAULT_MCP_HOST, preferred_port=busy_port, strict=False)
            self.assertNotEqual(chosen, busy_port)
            self.assertGreater(chosen, 0)

    def test_strict_mode_raises_when_preferred_port_is_busy(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
            occupied.bind((DEFAULT_MCP_HOST, 0))
            occupied.listen(1)
            busy_port = occupied.getsockname()[1]

            with self.assertRaises(OSError):
                pick_free_port(DEFAULT_MCP_HOST, preferred_port=busy_port, strict=True)


class WaitForPortAndLifecycleTests(unittest.TestCase):
    def test_wait_for_port_fails_fast_when_process_exits_early(self) -> None:
        with tempfile.TemporaryFile() as log_file:
            proc = subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stderr.write('boom startup\\n'); sys.exit(2)"],
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            try:
                with self.assertRaisesRegex(RuntimeError, r"(?s)exited unexpectedly with code 2.*boom startup"):
                    wait_for_port(
                        DEFAULT_MCP_HOST,
                        1,
                        proc=proc,
                        timeout=5.0,
                        poll_interval=0.02,
                        log_file=log_file,
                    )
            finally:
                proc.wait(timeout=5)

    def test_managed_cca_mcp_server_raises_clear_error_when_mcp_not_installed(self) -> None:
        with mock.patch("fca_compliance_agent.mcp_server.bootstrap.is_mcp_installed", return_value=False):
            with self.assertRaisesRegex(RuntimeError, r"extra mcp"):
                with managed_cca_mcp_server():
                    pass

    def test_managed_fca_mcp_server_raises_clear_error_when_handbook_rag_not_installed(self) -> None:
        with mock.patch("fca_compliance_agent.mcp_server.bootstrap.is_handbook_rag_installed", return_value=False):
            with self.assertRaisesRegex(RuntimeError, r"handbook-rag"):
                with managed_fca_mcp_server():
                    pass

    def test_managed_mcp_server_cleans_up_subprocess_on_exit(self) -> None:
        captured_procs: list[subprocess.Popen] = []
        real_popen = subprocess.Popen

        def tracking_popen(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            captured_procs.append(proc)
            return proc

        with (
            mock.patch("fca_compliance_agent.mcp_server.bootstrap.subprocess.Popen", side_effect=tracking_popen),
            mock.patch("fca_compliance_agent.mcp_server.bootstrap.wait_for_port", return_value=None),
        ):
            with managed_mcp_server(
                port=None,
                command=[sys.executable, "-c", "import time; time.sleep(30)"],
            ) as url:
                self.assertRegex(url, r"^http://127\.0\.0\.1:\d+/mcp$")
                self.assertEqual(len(captured_procs), 1)
                self.assertIsNone(captured_procs[0].poll())

            self.assertIsNotNone(captured_procs[0].poll())

    @unittest.skipUnless(is_mcp_installed() and _can_connect_loopback(), "needs mcp extra and loopback TCP connect")
    def test_managed_cca_mcp_server_serves_live_http_requests_and_shuts_down(self) -> None:
        from fca_compliance_agent.adapters.regulatory.mcp import (
            MCPConsumerCreditActAdapter,
            MCPServerConfig,
        )

        with managed_cca_mcp_server(port=None, startup_timeout=15.0) as url:
            config = MCPServerConfig(transport="streamable_http", url=url)

            async def query() -> None:
                cca = MCPConsumerCreditActAdapter(server_config=config)
                cca_hits = await cca.query_provisions("withdraw within 14 days", top_k=3)
                self.assertTrue(cca_hits)
                self.assertIn("CCA 1974 s.66A", [p.reference for p in cca_hits])

            asyncio.run(query())


class RunnerCliTests(unittest.TestCase):
    def test_runner_cli_defaults_and_flags(self) -> None:
        runner = _load_runner_module()
        parser = runner.build_parser()

        defaults = parser.parse_args([])
        self.assertEqual(defaults.module, "codebase_review")
        self.assertEqual(defaults.repo_url, "https://github.com/natchainsix/quicklend-nc")
        self.assertIsNone(defaults.fca_mcp_url)
        self.assertIsNone(defaults.fca_mcp_port)
        self.assertIsNone(defaults.cca_mcp_url)
        self.assertIsNone(defaults.cca_mcp_port)
        self.assertFalse(defaults.no_github)
        self.assertFalse(defaults.draft_pr)
        self.assertFalse(defaults.require_approval)
        self.assertFalse(hasattr(defaults, "no_mcp"))
        self.assertFalse(hasattr(defaults, "synthetic_diff"))

        default_payload = runner._build_payload(defaults)
        self.assertTrue(default_payload["raw_input"]["create_pr"])
        self.assertFalse(default_payload["raw_input"]["draft_pr"])

        parsed = parser.parse_args(
            [
                "--module",
                "pr_review",
                "--pr-url",
                "https://github.com/acme/credit/pull/7",
                "--fca-mcp-port",
                "8770",
                "--cca-mcp-port",
                "8010",
                "--require-approval",
            ]
        )
        self.assertEqual(parsed.module, "pr_review")
        self.assertEqual(parsed.fca_mcp_port, 8770)
        self.assertEqual(parsed.cca_mcp_port, 8010)
        self.assertTrue(parsed.require_approval)

        pr_payload = runner._build_payload(parsed)
        self.assertTrue(pr_payload["raw_input"]["post_to_github"])
        self.assertTrue(pr_payload["require_human_approval"])

        with self.assertRaises(ValueError):
            runner._build_payload(parser.parse_args(["--module", "pr_review"]))


if __name__ == "__main__":
    unittest.main()
