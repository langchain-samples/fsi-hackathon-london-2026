#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
RUNNER_SCRIPT="${REPO_ROOT}/04_fca_compliance_agent/scripts/run_draft_test.py"

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi

  for candidate in "${HOME}/.local/bin" "${HOME}/.cargo/bin"; do
    if [[ -x "${candidate}/uv" ]]; then
      export PATH="${candidate}:${PATH}"
      return 0
    fi
  done

  echo "uv not found on PATH; installing uv automatically..." >&2
  if ! command -v curl >/dev/null 2>&1; then
    echo "Error: curl is required to auto-install uv. Install uv manually: https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${HOME}/.cargo/bin:${PATH}"

  if ! command -v uv >/dev/null 2>&1; then
    echo "Error: uv installation finished, but uv is still not on PATH." >&2
    exit 1
  fi
}

has_flag() {
  local target="$1"
  shift
  for arg in "$@"; do
    if [[ "${arg}" == "${target}" ]]; then
      return 0
    fi
  done
  return 1
}

ensure_env_synced() {
  local venv_python="${REPO_ROOT}/.venv/bin/python"
  local check_expr="import fca_compliance_agent, mcp, qdrant_client, fastembed"
  local extras=(--extra mcp --extra handbook-rag)

  if [[ -n "${FCA_MCP_URL:-}" ]] || has_flag "--fca-mcp-url" "$@"; then
    check_expr="import fca_compliance_agent, mcp"
    extras=(--extra mcp)
  fi

  if [[ "${FORCE_UV_SYNC:-0}" != "1" && -x "${venv_python}" ]]; then
    if "${venv_python}" -c "${check_expr}" >/dev/null 2>&1; then
      return 0
    fi
  fi

  echo "Syncing fca-compliance-agent dependencies (${extras[*]}) with uv..." >&2
  if [[ -f "${REPO_ROOT}/uv.lock" ]]; then
    uv sync --frozen --inexact --package fca-compliance-agent "${extras[@]}"
  else
    uv sync --inexact --package fca-compliance-agent "${extras[@]}"
  fi
}

ensure_uv

if ! has_flag "-h" "$@" && ! has_flag "--help" "$@" && [[ ! -f "${REPO_ROOT}/.env" ]]; then
  echo "Warning: ${REPO_ROOT}/.env not found. Copy .env.example to .env and add your LangSmith gateway credentials." >&2
fi

cd "${REPO_ROOT}"
ensure_env_synced "$@"

exec uv run --no-sync --package fca-compliance-agent python "${RUNNER_SCRIPT}" "$@"
