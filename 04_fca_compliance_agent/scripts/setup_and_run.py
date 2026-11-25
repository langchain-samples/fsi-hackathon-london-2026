"""Single-command setup & end-to-end runner for the FCA & CCA Compliance Agent stack.

Invoked by `make run` (and `make setup` / `make ingest`). Automates:
  1. Repo `.env` bootstrap (with interactive prompts if LangSmith key is unset)
  2. Local embedding model download (`BAAI/bge-small-en-v1.5`) for FCA Handbook RAG
  3. FCA Handbook download + Qdrant indexing (prompts interactively for
     `FCA_HANDBOOK_API_TOKEN` and persists it to repo-root `.env.fca` if missing)
  4. `natchainsix/natchainsixagent-ui` clone, virtualenv install, and `.env` wiring
     to point directly at the real Compliance Agent HTTP API
  5. Starting and health-checking all 4 services with clean Ctrl+C shutdown:
       - FCA Handbook RAG MCP server      (`http://127.0.0.1:8765/mcp`)
       - Consumer Credit Act 1974 MCP     (`http://127.0.0.1:8001/mcp`)
       - FCA Compliance Agent HTTP API    (`http://127.0.0.1:8002`)
       - ReguLens Agent UI                (`http://127.0.0.1:8000`)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import httpx
from dotenv import dotenv_values

from fca_compliance_agent.mcp_server.bootstrap import (
    DEFAULT_CCA_MCP_PORT,
    DEFAULT_FCA_MCP_PORT,
    DEFAULT_MCP_HOST,
    is_port_listening,
    wait_for_port,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HANDBOOK_RAG_DIR = REPO_ROOT / "04_fca_compliance_agent" / "handbook_rag"
DEFAULT_ENV_FILE = REPO_ROOT / ".env"
DEFAULT_ENV_EXAMPLE = REPO_ROOT / ".env.example"
DEFAULT_FCA_ENV_FILE = REPO_ROOT / ".env.fca"
DEFAULT_UI_DIR = REPO_ROOT / "agent-ui"
DEFAULT_UI_REPO_HTTPS = "https://github.com/natchainsix/natchainsixagent-ui.git"
DEFAULT_UI_REPO_SSH = "git@github.com:natchainsix/natchainsixagent-ui.git"
DEFAULT_API_PORT = 8002
DEFAULT_UI_PORT = 8000
DEFAULT_GITHUB_ORG = "natchainsix"

_PLACEHOLDER_PATTERNS = (
    re.compile(r"^<.*>$"),
    re.compile(r"^replace[-_]with", re.IGNORECASE),
    re.compile(r"^your[-_]", re.IGNORECASE),
    re.compile(r"^changeme$", re.IGNORECASE),
)


# ---------------------------------------------------------------------------
# Environment File Helpers
# ---------------------------------------------------------------------------


def is_placeholder_value(value: str | None) -> bool:
    if value is None:
        return True
    cleaned = value.strip().strip('"').strip("'")
    if not cleaned:
        return True
    return any(pattern.match(cleaned) for pattern in _PLACEHOLDER_PATTERNS)


def upsert_env_value(path: Path, key: str, value: str, *, mode: int | None = None) -> None:
    """Insert or update `KEY=value` in a dotenv file while preserving other lines."""
    clean_value = value.strip()
    if any(ch in clean_value for ch in (" ", "#", '"', "'")) and not (
        (clean_value.startswith('"') and clean_value.endswith('"'))
        or (clean_value.startswith("'") and clean_value.endswith("'"))
    ):
        formatted = f"{key}='{clean_value}'"
    else:
        formatted = f"{key}={clean_value}"

    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")
    updated = False
    new_lines: list[str] = []
    for line in lines:
        if pattern.match(line):
            if not updated:
                new_lines.append(formatted)
                updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(formatted)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    if mode is not None:
        try:
            os.chmod(path, mode)
        except OSError:
            pass


def ensure_fca_api_token(
    env_fca_path: Path = DEFAULT_FCA_ENV_FILE,
    *,
    input_fn: Callable[[str], str] = input,
    interactive: bool | None = None,
) -> str:
    """Ensure `FCA_HANDBOOK_API_TOKEN` is persisted in `.env.fca`, prompting if absent."""
    existing = dotenv_values(env_fca_path).get("FCA_HANDBOOK_API_TOKEN") if env_fca_path.exists() else None
    if not is_placeholder_value(existing):
        assert existing is not None
        return existing.strip()

    is_tty = sys.stdin.isatty() if interactive is None else interactive
    if not is_tty:
        raise RuntimeError(
            f"FCA_HANDBOOK_API_TOKEN is not set in {env_fca_path} and stdin is not interactive. "
            "Get a free token at https://handbook.fca.org.uk/handbook-api and add "
            f"FCA_HANDBOOK_API_TOKEN=<your-token> to {env_fca_path}."
        )

    print("\n" + "=" * 70)
    print(" FCA Handbook API Token Required")
    print("=" * 70)
    print(f"FCA_HANDBOOK_API_TOKEN was not found in {env_fca_path}.")
    print("Get a free personal token at: https://handbook.fca.org.uk/handbook-api")
    print("The token will be saved to .env.fca (gitignored, permissions 0600).\n")

    token = ""
    for attempt in range(3):
        try:
            raw = input_fn("Enter your FCA_HANDBOOK_API_TOKEN: ")
        except EOFError as exc:
            raise RuntimeError("Aborted: no FCA_HANDBOOK_API_TOKEN provided.") from exc
        cleaned = raw.strip().strip('"').strip("'")
        if not is_placeholder_value(cleaned):
            token = cleaned
            break
        remaining = 2 - attempt
        if remaining > 0:
            print("Token cannot be empty or a placeholder. Please try again.")

    if not token:
        raise RuntimeError("No valid FCA_HANDBOOK_API_TOKEN provided.")

    upsert_env_value(env_fca_path, "FCA_HANDBOOK_API_TOKEN", token, mode=0o600)
    print(f"Saved FCA_HANDBOOK_API_TOKEN to {env_fca_path}.\n")
    return token


def ensure_repo_env(
    env_path: Path = DEFAULT_ENV_FILE,
    example_path: Path = DEFAULT_ENV_EXAMPLE,
    *,
    input_fn: Callable[[str], str] = input,
    interactive: bool | None = None,
) -> dict[str, str]:
    """Ensure repo-root `.env` exists and prompt for any missing backend settings."""
    if not env_path.exists() and example_path.exists():
        shutil.copyfile(example_path, env_path)
        try:
            os.chmod(env_path, 0o600)
        except OSError:
            pass
        print(f"Created {env_path.name} from {example_path.name}.")

    values = dict(dotenv_values(env_path)) if env_path.exists() else {}
    is_tty = sys.stdin.isatty() if interactive is None else interactive

    attendee = values.get("ATTENDEE_NAME")
    langsmith_key = values.get("LANGSMITH_API_KEY")
    openai_key = values.get("OPENAI_API_KEY")
    workspace_id = values.get("LANGSMITH_WORKSPACE_ID")
    model_name = values.get("MODEL_NAME")
    github_org = values.get("GITHUB_ORG")

    has_gateway_key = not is_placeholder_value(langsmith_key)
    has_direct_openai = (
        not is_placeholder_value(openai_key)
        and openai_key is not None
        and "${" not in openai_key
    )

    missing_required = not (has_gateway_key or has_direct_openai)
    missing_any = (
        missing_required
        or is_placeholder_value(attendee)
        or is_placeholder_value(workspace_id)
        or is_placeholder_value(model_name)
        or is_placeholder_value(github_org)
    )

    if missing_any and is_tty:
        print("\n" + "=" * 70)
        print(" Backend Configuration (.env)")
        print("=" * 70)
        if is_placeholder_value(attendee):
            try:
                entered = input_fn("Enter ATTENDEE_NAME [hackathon-user]: ").strip()
            except EOFError:
                entered = ""
            chosen = entered or "hackathon-user"
            upsert_env_value(env_path, "ATTENDEE_NAME", chosen, mode=0o600)
            values["ATTENDEE_NAME"] = chosen

        if missing_required:
            try:
                entered_key = input_fn("Enter LANGSMITH_API_KEY: ").strip().strip('"').strip("'")
            except EOFError:
                entered_key = ""
            if entered_key and not is_placeholder_value(entered_key):
                upsert_env_value(env_path, "LANGSMITH_API_KEY", entered_key, mode=0o600)
                values["LANGSMITH_API_KEY"] = entered_key
            else:
                print(
                    f"Warning: LANGSMITH_API_KEY left unset in {env_path}. Live LLM calls will fail until set.",
                    file=sys.stderr,
                )

        if is_placeholder_value(workspace_id):
            try:
                entered_ws = input_fn("Enter LANGSMITH_WORKSPACE_ID (optional, Enter to skip): ").strip()
            except EOFError:
                entered_ws = ""
            if entered_ws and not is_placeholder_value(entered_ws):
                upsert_env_value(env_path, "LANGSMITH_WORKSPACE_ID", entered_ws, mode=0o600)
                values["LANGSMITH_WORKSPACE_ID"] = entered_ws

        if is_placeholder_value(model_name):
            try:
                entered_model = input_fn("Enter MODEL_NAME [openai:gpt-6-luna]: ").strip()
            except EOFError:
                entered_model = ""
            chosen_model = entered_model or "openai:gpt-6-luna"
            upsert_env_value(env_path, "MODEL_NAME", chosen_model, mode=0o600)
            values["MODEL_NAME"] = chosen_model

        if is_placeholder_value(github_org):
            try:
                entered_org = input_fn(f"Enter GitHub org/owner for target discovery [{DEFAULT_GITHUB_ORG}]: ").strip()
            except EOFError:
                entered_org = ""
            chosen_org = entered_org or DEFAULT_GITHUB_ORG
            upsert_env_value(env_path, "GITHUB_ORG", chosen_org, mode=0o600)
            values["GITHUB_ORG"] = chosen_org
        print(f"Saved backend settings to {env_path}.\n")
    else:
        if missing_required:
            print(
                f"Warning: LANGSMITH_API_KEY is not configured in {env_path}. "
                "Live LLM reviews will require valid credentials in .env.",
                file=sys.stderr,
            )
        if is_placeholder_value(github_org):
            upsert_env_value(env_path, "GITHUB_ORG", DEFAULT_GITHUB_ORG, mode=0o600)
            values["GITHUB_ORG"] = DEFAULT_GITHUB_ORG

    return {k: str(v) for k, v in values.items() if v is not None}


def check_github_cli_auth(*, interactive: bool | None = None) -> None:
    """Warn clearly if `gh` is installed but not authenticated."""
    if not shutil.which("gh"):
        print(
            "Warning: GitHub CLI (`gh`) not found on PATH. Public GitHub discovery still works, "
            "but private repos, PR diffs, and remediation PRs require `gh` (https://cli.github.com).",
            file=sys.stderr,
        )
        return
    done = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if done.returncode != 0:
        is_tty = sys.stdin.isatty() if interactive is None else interactive
        print(
            "Note: `gh` CLI is not currently logged in. Run `gh auth login` to enable private repos, "
            "PR reviews, and opening remediation PRs."
            + (" (Continuing with public GitHub API fallback for discovery.)" if not is_tty else ""),
            file=sys.stderr,
        )


# ---------------------------------------------------------------------------
# FCA Handbook RAG Download & Ingestion Helpers
# ---------------------------------------------------------------------------


def get_qdrant_alias_target(
    rag_dir: Path = HANDBOOK_RAG_DIR,
    collection: str | None = None,
) -> str | None:
    """Inspect local Qdrant `meta.json` without acquiring the exclusive Qdrant lock."""
    alias = collection or os.environ.get("HANDBOOK_COLLECTION", "fca_handbook")
    qdrant_path = Path(os.environ.get("QDRANT_PATH") or (rag_dir / ".qdrant"))
    meta_path = qdrant_path / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    aliases = meta.get("aliases") or {}
    collections = meta.get("collections") or {}
    target = aliases.get(alias)
    if not target or target not in collections:
        return None
    return str(target)


def find_latest_complete_snapshot(rag_dir: Path = HANDBOOK_RAG_DIR) -> Path | None:
    """Return the newest snapshot directory containing both `provisions.jsonl` and `manifest.json`."""
    snapshots_root = rag_dir / "data" / "snapshots"
    if not snapshots_root.is_dir():
        return None
    candidates = [
        p
        for p in snapshots_root.iterdir()
        if p.is_dir() and (p / "provisions.jsonl").is_file() and (p / "manifest.json").is_file()
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.name, reverse=True)
    return candidates[0]


def ensure_embedding_model(rag_dir: Path = HANDBOOK_RAG_DIR) -> None:
    """Ensure the local FastEmbed embedding model is cached."""
    model_setting = os.environ.get("EMBEDDING_MODEL", "local:BAAI/bge-small-en-v1.5")
    if not model_setting.startswith("local:"):
        return
    print("Ensuring local FCA Handbook embedding model (BAAI/bge-small-en-v1.5)...")
    subprocess.run(
        [sys.executable, "-m", "utils.embeddings"],
        cwd=str(rag_dir),
        check=True,
    )


def ensure_handbook_ingested(
    *,
    rag_dir: Path = HANDBOOK_RAG_DIR,
    env_fca_path: Path = DEFAULT_FCA_ENV_FILE,
    force: bool = False,
    input_fn: Callable[[str], str] = input,
) -> str:
    """Ensure the real FCA Handbook is downloaded and indexed in Qdrant."""
    collection = os.environ.get("HANDBOOK_COLLECTION", "fca_handbook")
    if not force:
        live_target = get_qdrant_alias_target(rag_dir, collection)
        if live_target:
            print(f"FCA Handbook Qdrant index already ready: {collection} -> {live_target}")
            return live_target

    ensure_embedding_model(rag_dir)

    snapshot_dir = None if force else find_latest_complete_snapshot(rag_dir)
    if snapshot_dir is None:
        ensure_fca_api_token(env_fca_path, input_fn=input_fn)
        print("Downloading FCA Handbook sourcebooks (SYSC, PRIN, COBS, CONC, Glossary)...")
        print("(Resumable: already-downloaded chapters are reused automatically.)")
        subprocess.run(
            [sys.executable, "-m", "scripts.download"],
            cwd=str(rag_dir),
            check=True,
        )
        snapshot_dir = find_latest_complete_snapshot(rag_dir)
        if snapshot_dir is None:
            raise RuntimeError(
                f"FCA Handbook download completed but no snapshot was found under {rag_dir / 'data' / 'snapshots'}."
            )
    else:
        print(f"Using existing FCA Handbook snapshot: {snapshot_dir}")

    print(f"Ingesting FCA Handbook snapshot into Qdrant ({snapshot_dir.name})...")
    subprocess.run(
        [sys.executable, "-m", "scripts.ingest", str(snapshot_dir)],
        cwd=str(rag_dir),
        check=True,
    )

    live_target = get_qdrant_alias_target(rag_dir, collection)
    if not live_target:
        raise RuntimeError(
            f"FCA Handbook ingestion finished, but alias {collection!r} was not found in Qdrant metadata."
        )
    print(f"FCA Handbook Qdrant index ready: {collection} -> {live_target}")
    return live_target


# ---------------------------------------------------------------------------
# Agent UI Setup (`natchainsix/natchainsixagent-ui`)
# ---------------------------------------------------------------------------


def _find_uv_executable() -> str | None:
    if found := shutil.which("uv"):
        return found
    for candidate in (Path.home() / ".local" / "bin" / "uv", Path.home() / ".cargo" / "bin" / "uv"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def configure_agent_ui_env(
    ui_dir: Path,
    *,
    api_url: str,
    timeout_seconds: int = 300,
    input_fn: Callable[[str], str] = input,
    interactive: bool | None = None,
) -> Path:
    """Configure minimal `agent-ui/.env` (backend URL & timeout only; backend owns GitHub/MCP config).

    If `agent-ui/.env` does not exist or is missing `COMPLIANCE_API_URL` / `AGENT_TIMEOUT_SECONDS`,
    prompts interactively with sensible defaults and persists the values.
    """
    env_path = ui_dir / ".env"
    existed = env_path.exists()
    existing = dict(dotenv_values(env_path)) if existed else {}
    is_tty = sys.stdin.isatty() if interactive is None else interactive

    current_url = (existing.get("COMPLIANCE_API_URL") or "").strip()
    current_timeout = (existing.get("AGENT_TIMEOUT_SECONDS") or "").strip()
    current_mode = (existing.get("MODE") or "").strip()

    chosen_url = current_url or api_url.rstrip("/")
    chosen_timeout = current_timeout or str(timeout_seconds)

    if is_tty and (not existed or not current_url or not current_timeout):
        print("\n" + "=" * 70)
        print(" Agent UI Configuration (agent-ui/.env)")
        print("=" * 70)
        print("The backend owns GitHub discovery (via `gh`), MCPs, and LLM keys.")
        print("The UI only needs the compliance backend URL and request timeout.\n")
        if not current_url:
            try:
                entered_url = input_fn(f"Enter COMPLIANCE_API_URL [{api_url.rstrip('/')}]: ").strip()
            except EOFError:
                entered_url = ""
            chosen_url = (entered_url or api_url).rstrip("/")
        if not current_timeout:
            try:
                entered_timeout = input_fn(f"Enter AGENT_TIMEOUT_SECONDS [{timeout_seconds}]: ").strip()
            except EOFError:
                entered_timeout = ""
            chosen_timeout = entered_timeout if entered_timeout.isdigit() else str(timeout_seconds)
        print(f"Saved UI settings to {env_path}.\n")

    chosen_mode = current_mode if current_mode in {"backend", "github"} else "backend"
    upsert_env_value(env_path, "MODE", chosen_mode)
    upsert_env_value(env_path, "COMPLIANCE_API_URL", chosen_url)
    upsert_env_value(env_path, "AGENT_TIMEOUT_SECONDS", chosen_timeout)
    return env_path


def ensure_agent_ui(
    *,
    ui_dir: Path = DEFAULT_UI_DIR,
    repo_url: str | None = None,
    branch: str | None = None,
    api_url: str = f"http://{DEFAULT_MCP_HOST}:{DEFAULT_API_PORT}",
    input_fn: Callable[[str], str] = input,
    interactive: bool | None = None,
) -> Path:
    """Clone `natchainsix/natchainsixagent-ui`, install its venv, and wire `.env`."""
    if not (ui_dir / "app.py").is_file():
        candidates = [repo_url] if repo_url else [DEFAULT_UI_REPO_HTTPS, DEFAULT_UI_REPO_SSH]
        last_error: str | None = None
        for candidate in candidates:
            cmd = ["git", "clone"]
            if branch:
                cmd += ["--branch", branch]
            cmd += [candidate, str(ui_dir)]
            print(f"Cloning Agent UI from {candidate} into {ui_dir}...")
            done = subprocess.run(cmd, capture_output=True, text=True)
            if done.returncode == 0:
                last_error = None
                break
            last_error = (done.stderr or done.stdout).strip()
            if ui_dir.exists() and not (ui_dir / "app.py").is_file():
                shutil.rmtree(ui_dir, ignore_errors=True)
        if last_error is not None:
            raise RuntimeError(f"Failed to clone Agent UI repository: {last_error}")

    venv_python = ui_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv_bin = _find_uv_executable()

    needs_install = not venv_python.is_file()
    if not needs_install:
        check = subprocess.run(
            [str(venv_python), "-c", "import fastapi, uvicorn, httpx, dotenv"],
            capture_output=True,
        )
        needs_install = check.returncode != 0

    if needs_install:
        print(f"Installing Agent UI dependencies in {ui_dir / '.venv'}...")
        if uv_bin:
            if not venv_python.is_file():
                subprocess.run([uv_bin, "venv", str(ui_dir / ".venv")], check=True)
            subprocess.run(
                [uv_bin, "pip", "install", "--python", str(venv_python), "-r", str(ui_dir / "requirements.txt")],
                check=True,
            )
        else:
            if not venv_python.is_file():
                subprocess.run([sys.executable, "-m", "venv", str(ui_dir / ".venv")], check=True)
            subprocess.run(
                [str(venv_python), "-m", "pip", "install", "-r", str(ui_dir / "requirements.txt")],
                check=True,
            )

    configure_agent_ui_env(ui_dir, api_url=api_url, input_fn=input_fn, interactive=interactive)
    print(f"Agent UI ready at {ui_dir} (wired to {api_url}).")
    return venv_python


# ---------------------------------------------------------------------------
# Multi-Service Process Supervision
# ---------------------------------------------------------------------------


@dataclass
class ManagedService:
    name: str
    url: str
    proc: subprocess.Popen[Any] | None
    log_file: IO[bytes] | None


def _start_or_reuse_service(
    *,
    name: str,
    host: str,
    port: int,
    url: str,
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout: float = 30.0,
) -> ManagedService:
    if is_port_listening(host, port, timeout=0.35):
        print(f"[reused]  {name}: {url}")
        return ManagedService(name=name, url=url, proc=None, log_file=None)

    log_file = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    try:
        wait_for_port(host, port, proc=proc, timeout=timeout, log_file=log_file)
    except Exception:
        _stop_service(ManagedService(name=name, url=url, proc=proc, log_file=log_file))
        raise
    print(f"[started] {name}: {url}")
    return ManagedService(name=name, url=url, proc=proc, log_file=log_file)


def _stop_service(service: ManagedService) -> None:
    if service.proc is not None and service.proc.poll() is None:
        try:
            service.proc.terminate()
            service.proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            service.proc.kill()
            service.proc.wait(timeout=5.0)
        except OSError:
            pass
    if service.log_file is not None:
        try:
            service.log_file.close()
        except OSError:
            pass


def _read_tail(log_file: IO[bytes] | None, max_chars: int = 2000) -> str:
    if log_file is None:
        return ""
    try:
        log_file.flush()
        log_file.seek(0)
        text = log_file.read().decode("utf-8", errors="replace").strip()
        return text[-max_chars:] if len(text) > max_chars else text
    except OSError:
        return ""


def _verify_stack_health(api_url: str, ui_url: str) -> dict[str, Any]:
    with httpx.Client(timeout=10.0) as client:
        api_health = client.get(f"{api_url.rstrip('/')}/health")
        api_health.raise_for_status()
        health_data = api_health.json()

        ui_status = client.get(f"{ui_url.rstrip('/')}/api/status")
        ui_status.raise_for_status()
    return health_data


def run_stack(args: argparse.Namespace) -> None:
    host = args.host
    fca_url = f"http://{host}:{args.fca_mcp_port}/mcp"
    cca_url = f"http://{host}:{args.cca_mcp_port}/mcp"
    api_url = f"http://{host}:{args.api_port}"
    ui_url = f"http://{host}:{args.ui_port}"
    github_org = args.github_org or os.environ.get("GITHUB_ORG") or DEFAULT_GITHUB_ORG

    # 1. Repo .env + gh check + FCA Handbook download/ingest
    repo_env = ensure_repo_env()
    github_org = args.github_org or repo_env.get("GITHUB_ORG") or os.environ.get("GITHUB_ORG") or DEFAULT_GITHUB_ORG
    check_github_cli_auth()
    if not args.skip_ingest:
        ensure_handbook_ingested(force=args.force_ingest)
    if args.ingest_only:
        return

    # 2. Agent UI setup (only backend URL + timeout in agent-ui/.env)
    ui_python: Path | None = None
    if not args.skip_ui:
        ui_python = ensure_agent_ui(
            ui_dir=Path(args.ui_dir),
            repo_url=args.ui_repo,
            branch=args.ui_branch,
            api_url=api_url,
        )

    if args.setup_only:
        print("\nSetup complete! Run `make run` to start all servers end-to-end.")
        return

    # 3. Launch all services in dependency order
    services: list[ManagedService] = []
    base_env = os.environ.copy()
    base_env["FCA_MCP_URL"] = fca_url
    base_env["CCA_MCP_URL"] = cca_url
    base_env["GITHUB_ORG"] = github_org

    stop_requested = False

    def _handle_signal(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, _handle_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_signal)

    print("\nStarting compliance stack...")
    try:
        services.append(
            _start_or_reuse_service(
                name="FCA Handbook RAG MCP",
                host=host,
                port=args.fca_mcp_port,
                url=fca_url,
                cmd=[sys.executable, "mcp_server.py", "--port", str(args.fca_mcp_port)],
                cwd=HANDBOOK_RAG_DIR,
                env=base_env,
                timeout=30.0,
            )
        )
        services.append(
            _start_or_reuse_service(
                name="Consumer Credit Act MCP",
                host=host,
                port=args.cca_mcp_port,
                url=cca_url,
                cmd=[
                    sys.executable,
                    "-m",
                    "fca_compliance_agent.mcp_server.server",
                    "--http",
                    "--host",
                    host,
                    "--port",
                    str(args.cca_mcp_port),
                ],
                cwd=REPO_ROOT,
                env=base_env,
                timeout=20.0,
            )
        )
        services.append(
            _start_or_reuse_service(
                name="FCA Compliance Agent API",
                host=host,
                port=args.api_port,
                url=api_url,
                cmd=[
                    sys.executable,
                    "-m",
                    "fca_compliance_agent.adapters.http_api",
                    "--host",
                    host,
                    "--port",
                    str(args.api_port),
                    "--github-org",
                    github_org,
                ],
                cwd=REPO_ROOT,
                env=base_env,
                timeout=25.0,
            )
        )
        if not args.skip_ui and ui_python is not None:
            services.append(
                _start_or_reuse_service(
                    name="ReguLens Agent UI",
                    host=host,
                    port=args.ui_port,
                    url=ui_url,
                    cmd=[
                        str(ui_python),
                        "-m",
                        "uvicorn",
                        "app:app",
                        "--host",
                        host,
                        "--port",
                        str(args.ui_port),
                        "--workers",
                        "1",
                    ],
                    cwd=Path(args.ui_dir),
                    env=os.environ.copy(),
                    timeout=25.0,
                )
            )

        health = _verify_stack_health(api_url, ui_url) if not args.skip_ui else {}
        print("\n" + "=" * 72)
        print(" FCA & CCA Compliance Agent Stack is LIVE")
        print("=" * 72)
        if not args.skip_ui:
            print(f" ReguLens Agent UI:        {ui_url}")
        print(f" Compliance Agent API:     {api_url}  (/health, /api/targets, /api/findings, /api/runs, /review)")
        print(f" FCA Handbook RAG MCP:     {fca_url}  (reachable={health.get('fca_mcp_reachable', True)})")
        print(f" Consumer Credit Act MCP:  {cca_url}  (reachable={health.get('cca_mcp_reachable', True)})")
        print("=" * 72)

        if args.smoke_check:
            print("Smoke check succeeded; shutting down managed services.")
            return

        print("Press Ctrl+C to stop all services started by `make run`.\n")
        while not stop_requested:
            for svc in services:
                if svc.proc is not None and svc.proc.poll() is not None:
                    logs = _read_tail(svc.log_file)
                    raise RuntimeError(
                        f"{svc.name} exited unexpectedly with code {svc.proc.returncode}.\n{logs}"
                    )
            time.sleep(0.5)
    finally:
        if services:
            print("\nShutting down managed services...")
        for svc in reversed(services):
            _stop_service(svc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-command setup and end-to-end launcher for the FCA & CCA Compliance Agent stack."
    )
    parser.add_argument("--host", default=DEFAULT_MCP_HOST, help=f"Bind host (default: {DEFAULT_MCP_HOST}).")
    parser.add_argument("--fca-mcp-port", type=int, default=DEFAULT_FCA_MCP_PORT)
    parser.add_argument("--cca-mcp-port", type=int, default=DEFAULT_CCA_MCP_PORT)
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--ui-port", type=int, default=DEFAULT_UI_PORT)
    parser.add_argument("--github-org", default=None, help=f"Default GitHub org (default: {DEFAULT_GITHUB_ORG}).")
    parser.add_argument("--ui-dir", default=str(DEFAULT_UI_DIR), help=f"Agent UI directory (default: {DEFAULT_UI_DIR}).")
    parser.add_argument("--ui-repo", default=os.environ.get("AGENT_UI_REPO") or None)
    parser.add_argument("--ui-branch", default=os.environ.get("AGENT_UI_BRANCH") or None)
    parser.add_argument("--setup-only", action="store_true", help="Run setup & ingestion only without starting servers.")
    parser.add_argument("--ingest-only", action="store_true", help="Run FCA Handbook download & ingestion only.")
    parser.add_argument("--force-ingest", action="store_true", help="Re-download and re-ingest the FCA Handbook.")
    parser.add_argument("--skip-ingest", action="store_true", help="Skip FCA Handbook ingestion check.")
    parser.add_argument("--skip-ui", action="store_true", help="Start MCP servers and Agent API without Agent UI.")
    parser.add_argument("--smoke-check", action="store_true", help="Start stack, verify health endpoints, then exit.")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        run_stack(args)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
