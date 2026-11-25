SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c

UV_BIN := $(shell command -v uv 2>/dev/null || ([ -x "$$HOME/.local/bin/uv" ] && echo "$$HOME/.local/bin/uv") || ([ -x "$$HOME/.cargo/bin/uv" ] && echo "$$HOME/.cargo/bin/uv"))
EXTRAS := --extra mcp --extra handbook-rag --extra dev

.DEFAULT_GOAL := run

.PHONY: help ensure-uv sync setup ingest run test

help: ## Show available targets
	@echo "FCA & CCA Compliance Agent — Single-Command Setup & Runner"
	@echo ""
	@echo "Targets:"
	@echo "  make run     Set up everything (uv, deps, FCA Handbook RAG, MCPs, Agent API, Agent UI) and run end-to-end"
	@echo "  make setup   Set up uv, deps, embedding model, FCA Handbook RAG index, and Agent UI without starting servers"
	@echo "  make ingest  Download (prompting for FCA API token if needed) and ingest the FCA Handbook into Qdrant"
	@echo "  make test    Run the FCA Compliance Agent and Handbook RAG test suites"

ensure-uv: ## Install uv automatically if it is not already on PATH
	@if command -v uv >/dev/null 2>&1 || [ -x "$$HOME/.local/bin/uv" ] || [ -x "$$HOME/.cargo/bin/uv" ]; then \
		exit 0; \
	fi; \
	echo "uv not found on PATH; installing uv automatically..."; \
	if ! command -v curl >/dev/null 2>&1; then \
		echo "Error: curl is required to install uv. See https://docs.astral.sh/uv/getting-started/installation/" >&2; \
		exit 1; \
	fi; \
	curl -LsSf https://astral.sh/uv/install.sh | sh

sync: ensure-uv ## Sync all workspace packages and compliance/MCP/RAG extras into .venv
	@export PATH="$$HOME/.local/bin:$$HOME/.cargo/bin:$$PATH"; \
	echo "Syncing workspace dependencies with uv..."; \
	if [ -f uv.lock ]; then \
		uv sync --frozen --all-packages $(EXTRAS); \
	else \
		uv sync --all-packages $(EXTRAS); \
	fi

setup: sync ## Set up .env, FCA Handbook RAG download/ingest, and Agent UI without launching servers
	@export PATH="$$HOME/.local/bin:$$HOME/.cargo/bin:$$PATH"; \
	uv run --no-sync --package fca-compliance-agent python 04_fca_compliance_agent/scripts/setup_and_run.py --setup-only $(ARGS)

ingest: sync ## Ensure FCA Handbook is downloaded and indexed in Qdrant (prompts for .env.fca token if missing)
	@export PATH="$$HOME/.local/bin:$$HOME/.cargo/bin:$$PATH"; \
	uv run --no-sync --package fca-compliance-agent python 04_fca_compliance_agent/scripts/setup_and_run.py --ingest-only $(ARGS)

run: sync ## Full end-to-end setup and launch: FCA RAG + MCP servers + Compliance Agent API + ReguLens Agent UI
	@export PATH="$$HOME/.local/bin:$$HOME/.cargo/bin:$$PATH"; \
	uv run --no-sync --package fca-compliance-agent python 04_fca_compliance_agent/scripts/setup_and_run.py $(ARGS)

test: sync ## Run compliance agent and handbook RAG tests
	@export PATH="$$HOME/.local/bin:$$HOME/.cargo/bin:$$PATH"; \
	uv run --no-sync --package fca-compliance-agent python -m unittest discover -s 04_fca_compliance_agent/tests -v; \
	cd 04_fca_compliance_agent/handbook_rag && uv run --no-sync pytest -q
