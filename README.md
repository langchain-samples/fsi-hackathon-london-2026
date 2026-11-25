# FSI Hackathon Workshops

Hands-on notebooks, with mirrored `.py` files, for building financial-services agents with LangChain, LangGraph
and LangSmith. Each topic has its own folder and README:

| Folder | Topic | What you build |
|--------|-------|----------------|
| [`00_dcode_setup`](00_dcode_setup/README.md) | Deep Agents Code setup | Install and configure `dcode`, a terminal coding agent that helps you build and debug the agents below (optional) |
| [`01_kyc_agent`](01_kyc_agent/README.md) | KYC onboarding | A LangGraph agent that extracts application details, screens against sanctions and PEP lists, and scores risk |
| [`02_transaction_monitoring_agent`](02_transaction_monitoring_agent/README.md) | Transaction monitoring | A deep agent with memory and human review, then evaluated and improved with LangSmith |
| [`03_document_summary_agent`](03_document_summary_agent/README.md) | Compliance brief | A LangGraph map-reduce workflow that summarises a long regulatory document into a reviewable brief |
| [`04_fca_handbook_rag`](04_fca_handbook_rag/README.md) | FCA Handbook RAG | Search over the FCA Handbook with Qdrant, for agents that assess pull requests |

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended)
- **Windows only:** the Microsoft Visual C++ runtime and build tools (install
  [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) with the "Desktop
  development with C++" workload). Some packages have to compile during install.

## Setup

All three topics share one Python environment, `.venv` in this folder. Install it once, from the top level folder:

```bash
uv sync --all-packages
```

To work through a topic, `cd` into its folder and follow that folder's README. For example, for the KYC agent:

```bash
cd 01_kyc_agent
```

Then follow [`01_kyc_agent/README.md`](01_kyc_agent/README.md).

## Deep Agents Code (optional)

Deep Agents Code (`dcode`) is a terminal coding agent you can use alongside the workshops. It is installed as a
standalone tool, separate from the shared `.venv`, and keeps its own settings in `~/.deepagents/.env`:

```bash
uv tool install deepagents-code
mkdir -p ~/.deepagents
cp 00_dcode_setup/.env.example ~/.deepagents/.env
chmod 600 ~/.deepagents/.env
# Edit ~/.deepagents/.env: set ATTENDEE_NAME, your LangSmith API key and workspace ID
```

Then run `dcode` from any topic folder. See [`00_dcode_setup/README.md`](00_dcode_setup/README.md) for the full
setup and troubleshooting.

## Environment variables

All three topics (`01_kyc_agent`, `02_transaction_monitoring_agent`, `03_document_summary_agent`) share one `.env` in this folder:

```bash
cp .env.example .env
# Edit .env and fill in your keys
```

The agents reach their model through the LangSmith LLM Gateway set up in `.env`. They use `openai:gpt-6-terra` by
default; set `MODEL_NAME` (as `provider:model`) in `.env` to choose another.

## Project layout

Each sub project contains two things to get you going:

- **Notebooks** (in `modules/`) that explains the concepts behind building that kind of agent, with concise
  examples.
- **Python starter code** (`agent.py`, with its `tools/` and `utils/`) to get you started with the project.
