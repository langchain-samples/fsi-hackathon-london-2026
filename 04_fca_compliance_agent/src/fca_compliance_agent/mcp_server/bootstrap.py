"""Lifecycle helpers for auto-bootstrapping the local regulatory MCP server in the background."""

from __future__ import annotations

import atexit
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import IO, Any

DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_MCP_PORT = 8001


def is_mcp_installed() -> bool:
    """Return True if the optional `mcp` dependency is importable."""
    return importlib.util.find_spec("mcp") is not None


def pick_free_port(
    host: str = DEFAULT_MCP_HOST,
    *,
    preferred_port: int | None = DEFAULT_MCP_PORT,
    strict: bool = False,
) -> int:
    """Return an available TCP port on `host`.

    Tries `preferred_port` first when provided. If it is already in use and
    `strict` is False, falls back to an OS-assigned ephemeral free port.
    """
    if preferred_port is not None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, preferred_port))
                return preferred_port
            except OSError as exc:
                if strict:
                    raise OSError(
                        f"Requested MCP port {preferred_port} on {host} is already in use."
                    ) from exc

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _read_log_tail(log_file: IO[bytes] | None, max_chars: int = 2000) -> str:
    if log_file is None:
        return ""
    try:
        log_file.flush()
        log_file.seek(0)
        text = log_file.read().decode("utf-8", errors="replace").strip()
        return text[-max_chars:] if len(text) > max_chars else text
    except OSError:
        return ""


def wait_for_port(
    host: str,
    port: int,
    *,
    proc: subprocess.Popen[Any] | None = None,
    timeout: float = 20.0,
    poll_interval: float = 0.1,
    log_file: IO[bytes] | None = None,
) -> None:
    """Block until `(host, port)` accepts TCP connections or `proc` exits early."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            logs = _read_log_tail(log_file)
            detail = f"\nServer output:\n{logs}" if logs else ""
            raise RuntimeError(
                f"Background MCP server exited unexpectedly with code {proc.returncode}.{detail}"
            )
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return
        except OSError:
            time.sleep(poll_interval)

    logs = _read_log_tail(log_file)
    detail = f"\nServer output:\n{logs}" if logs else ""
    raise TimeoutError(
        f"Timed out after {timeout:.1f}s waiting for MCP server at http://{host}:{port}/mcp.{detail}"
    )


def _terminate_process(proc: subprocess.Popen[Any], grace_seconds: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        return
    try:
        proc.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
            proc.wait(timeout=5.0)
        except OSError:
            pass


def resolve_mcp_mode(
    *,
    mcp_url: str | None,
    no_mcp: bool,
    environ: Mapping[str, str] | None = None,
) -> tuple[str | None, bool]:
    """Decide how regulatory MCP connectivity should be configured.

    Returns `(external_url, should_bootstrap)`:
      - `(None, False)` -> mock adapters (`--no-mcp`)
      - `(url, False)`  -> use an already-configured external MCP server URL
      - `(None, True)`  -> auto-bootstrap the local MCP server in the background
    """
    if no_mcp:
        return None, False
    if mcp_url:
        return mcp_url, False

    env = os.environ if environ is None else environ
    existing_url = env.get("CCA_MCP_URL") or env.get("FCA_MCP_URL")
    if existing_url:
        return existing_url, False

    return None, True


@contextmanager
def managed_mcp_server(
    *,
    host: str = DEFAULT_MCP_HOST,
    port: int | None = DEFAULT_MCP_PORT,
    strict_port: bool = False,
    startup_timeout: float = 20.0,
    command: list[str] | None = None,
) -> Iterator[str]:
    """Start `fca-regulatory-mcp --http` in the background and stop it on exit.

    Yields the Streamable HTTP endpoint URL (e.g. `http://127.0.0.1:8001/mcp`).
    """
    if command is None and not is_mcp_installed():
        raise RuntimeError(
            "The optional `mcp` package is required to bootstrap the regulatory MCP server. "
            "Run via `./04_fca_compliance_agent/scripts/run.sh`, install with "
            "`uv sync --frozen --inexact --package fca-compliance-agent --extra mcp`, "
            "or pass `--no-mcp` to use in-memory mock adapters."
        )

    chosen_port = pick_free_port(host, preferred_port=port, strict=strict_port)
    cmd = command or [
        sys.executable,
        "-m",
        "fca_compliance_agent.mcp_server.server",
        "--http",
        "--host",
        host,
        "--port",
        str(chosen_port),
    ]

    log_file = tempfile.TemporaryFile()
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)

    def _atexit_cleanup() -> None:
        _terminate_process(proc)

    atexit.register(_atexit_cleanup)
    try:
        wait_for_port(
            host,
            chosen_port,
            proc=proc,
            timeout=startup_timeout,
            log_file=log_file,
        )
        yield f"http://{host}:{chosen_port}/mcp"
    finally:
        atexit.unregister(_atexit_cleanup)
        _terminate_process(proc)
        log_file.close()
