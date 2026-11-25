#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [[ ! -f "${REPO_ROOT}/.env" ]]; then
  echo "Warning: ${REPO_ROOT}/.env not found. Copy .env.example to .env and add your LangSmith gateway credentials." >&2
fi

cd "${REPO_ROOT}"

if [[ -d "${REPO_ROOT}/.venv" ]]; then
  exec uv run --no-sync --package fca-compliance-agent python "${REPO_ROOT}/04_fca_compliance_agent/scripts/run_draft_test.py" "$@"
else
  exec uv run --package fca-compliance-agent python "${REPO_ROOT}/04_fca_compliance_agent/scripts/run_draft_test.py" "$@"
fi
