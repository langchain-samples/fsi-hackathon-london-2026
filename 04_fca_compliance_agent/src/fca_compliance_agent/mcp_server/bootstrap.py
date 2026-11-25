"""Lifecycle helpers for auto-bootstrapping the local regulatory MCP servers in the background.

Manages both real regulatory MCP servers:
  - Consumer Credit Act 1974 MCP server (`fca-regulatory-mcp --http`, default port 8001)
  - FCA Handbook RAG MCP server (`handbook_rag/mcp_server.py`, default port 8765)
"""

from __future__ import annotations

import atexit
import importlib.util
import os
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

DEFAULT_MCP_HOST = "127.0.0.1"
DEFAULT_CCA_MCP_PORT = 8001
DEFAULT_MCP_PORT = DEFAULT_CCA_MCP_PORT
DEFAULT_FCA_MCP_PORT = 8765
DEFAULT_FCA_MCP_URL = f"http://{DEFAULT_MCP_HOST}:{DEFAULT_FCA_MCP_PORT}/mcp"

HANDBOOK_RAG_DIR = Path(__file__).resolve().parents[3] / "handbook_rag"


def is_mcp_installed() -> bool:
    """Return True if the optional `mcp` dependency is importable."""
    return importlib.util.find_spec("mcp") is not None


def is_handbook_rag_installed() -> bool:
    """Return True if the optional `handbook-rag` dependencies are importable."""
    return all(
        importlib.util.find_spec(pkg) is not None
        for pkg in ("mcp", "qdrant_client", "fastembed")
    )


def is_port_listening(host: str, port: int, *, timeout: float = 0.25) -> bool:
    """Return True if a TCP server is already accepting connections at `(host, port)`."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def pick_free_port(
    host: str = DEFAULT_MCP_HOST,
    *,
    preferred_port: int | None = DEFAULT_CCA_MCP_PORT,
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
        if is_port_listening(host, port, timeout=0.25):
            return
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


def resolve_cca_mcp_mode(
    *,
    cca_mcp_url: str | None,
    environ: Mapping[str, str] | None = None,
) -> tuple[str | None, bool]:
    """Decide how Consumer Credit Act MCP connectivity should be configured.

    Returns `(external_url, should_bootstrap)`:
      - `(url, False)` -> connect to an already-configured CCA MCP server URL
      - `(None, True)` -> auto-bootstrap the local `fca-regulatory-mcp` HTTP server
    """
    if cca_mcp_url:
        return cca_mcp_url, False

    env = os.environ if environ is None else environ
    existing_url = env.get("CCA_MCP_URL")
    if existing_url:
        return existing_url, False

    return None, True


def resolve_fca_mcp_mode(
    *,
    fca_mcp_url: str | None,
    environ: Mapping[str, str] | None = None,
    check_default_port: Callable[[str, int], bool] | None = None,
) -> tuple[str | None, bool]:
    """Decide how FCA Handbook RAG MCP connectivity should be configured.

    Returns `(external_url, should_bootstrap)`:
      - `(url, False)` -> connect to an explicit or already-running FCA Handbook RAG MCP server
      - `(None, True)` -> auto-bootstrap `04_fca_compliance_agent/handbook_rag/mcp_server.py`
    """
    if fca_mcp_url:
        return fca_mcp_url, False

    env = os.environ if environ is None else environ
    existing_url = env.get("FCA_MCP_URL")
    if existing_url:
        return existing_url, False

    probe = check_default_port or is_port_listening
    if probe(DEFAULT_MCP_HOST, DEFAULT_FCA_MCP_PORT):
        return DEFAULT_FCA_MCP_URL, False

    return None, True


@contextmanager
def _run_managed_http_server(
    *,
    host: str,
    port: int,
    cmd: list[str],
    cwd: Path | None = None,
    startup_timeout: float = 20.0,
) -> Iterator[str]:
    log_file = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )

    def _atexit_cleanup() -> None:
        _terminate_process(proc)

    atexit.register(_atexit_cleanup)
    try:
        wait_for_port(
            host,
            port,
            proc=proc,
            timeout=startup_timeout,
            log_file=log_file,
        )
        yield f"http://{host}:{port}/mcp"
    finally:
        atexit.unregister(_atexit_cleanup)
        _terminate_process(proc)
        log_file.close()


@contextmanager
def managed_mcp_server(
    *,
    host: str = DEFAULT_MCP_HOST,
    port: int | None = DEFAULT_CCA_MCP_PORT,
    strict_port: bool = False,
    startup_timeout: float = 20.0,
    command: list[str] | None = None,
) -> Iterator[str]:
    """Start `fca-regulatory-mcp --http` (CCA 1974) in the background and stop it on exit.

    Yields the Streamable HTTP endpoint URL (e.g. `http://127.0.0.1:8001/mcp`).
    """
    if command is None and not is_mcp_installed():
        raise RuntimeError(
            "The optional `mcp` package is required to bootstrap the CCA 1974 MCP server. "
            "Run via `./04_fca_compliance_agent/scripts/run.sh` or install with "
            "`uv sync --frozen --inexact --package fca-compliance-agent --extra mcp`."
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
    with _run_managed_http_server(
        host=host,
        port=chosen_port,
        cmd=cmd,
        startup_timeout=startup_timeout,
    ) as url:
        yield url


managed_cca_mcp_server = managed_mcp_server


@contextmanager
def managed_fca_mcp_server(
    *,
    host: str = DEFAULT_MCP_HOST,
    port: int | None = DEFAULT_FCA_MCP_PORT,
    strict_port: bool = False,
    startup_timeout: float = 30.0,
    command: list[str] | None = None,
    handbook_rag_dir: Path | None = None,
) -> Iterator[str]:
    """Start the real FCA Handbook RAG MCP server (`handbook_rag/mcp_server.py`) and stop it on exit.

    Yields the Streamable HTTP endpoint URL (e.g. `http://127.0.0.1:8765/mcp`).
    """
    if command is None and not is_handbook_rag_installed():
        raise RuntimeError(
            "The optional `handbook-rag` packages (`mcp`, `qdrant-client`, `fastembed`) are required "
            "to bootstrap the FCA Handbook RAG MCP server. Install them with "
            "`uv sync --all-packages --extra handbook-rag`, or start `handbook_rag/mcp_server.py` "
            "separately and set `FCA_MCP_URL`."
        )

    rag_dir = handbook_rag_dir or HANDBOOK_RAG_DIR
    if command is None and not (rag_dir / "mcp_server.py").is_file():
        raise RuntimeError(
            f"FCA Handbook RAG server script not found at {rag_dir / 'mcp_server.py'}. "
            "Set `FCA_MCP_URL` to a running FCA Handbook RAG MCP server."
        )

    chosen_port = pick_free_port(host, preferred_port=port, strict=strict_port)
    cmd = command or [
        sys.executable,
        "mcp_server.py",
        "--port",
        str(chosen_port),
    ]
    with _run_managed_http_server(
        host=host,
        port=chosen_port,
        cmd=cmd,
        cwd=None if command is not None else rag_dir,
        startup_timeout=startup_timeout,
    ) as url:
        yield url
