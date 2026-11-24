# MillTech: FinCrime Control Tower

Our team's fork of LangChain's FSI hackathon repo (London, 25 November 2026). We build all three starter projects and
join them with a supervisor deep agent and one shared memory: **a new regulation lands, the system turns it into policy,
KYC and monitoring behaviour changes, evals prove it, and a human signs off where it matters.**

| Folder | What | Owner |
|--------|------|-------|
| [`00_control_tower`](00_control_tower/README.md) | Supervisor deep agent, shared memory, demo portal, evals. **Submission: [`control_tower.ipynb`](00_control_tower/control_tower.ipynb) + [`agents.py`](00_control_tower/agents.py)** | Lukman |
| [`01_kyc_agent`](01_kyc_agent/README.md) | KYC onboarding (LangGraph) | Nam |
| [`02_transaction_monitoring_agent`](02_transaction_monitoring_agent/README.md) | Transaction monitoring (agent with memory and human review) | Marcus |
| [`03_document_summary_agent`](03_document_summary_agent/README.md) | Regulatory change / compliance brief (deep agent with sub-agents) | Alejandro |

Start here: [game plan](docs/GAME_PLAN.md) · [how we work (setup, branches, PRs)](CONTRIBUTING.md) ·
[process maps](docs/process_maps/README.md). All data is synthetic; outputs are drafts for a human reviewer, not
compliance advice.

---

# FSI Hackathon Workshops

Hands-on notebooks, with mirrored `.py` files, for building financial-services agents with LangChain, LangGraph
and LangSmith. Each topic has its own folder and README:

| Folder | Topic | What you build |
|--------|-------|----------------|
| [`01_kyc_agent`](01_kyc_agent/README.md) | KYC onboarding | A LangGraph agent that extracts application details, screens against sanctions and PEP lists, and scores risk |
| [`02_transaction_monitoring_agent`](02_transaction_monitoring_agent/README.md) | Transaction monitoring | A deep agent with memory and human review, then evaluated and improved with LangSmith |
| [`03_document_summary_agent`](03_document_summary_agent/README.md) | Compliance brief | A LangGraph map-reduce workflow that summarises a long regulatory document into a reviewable brief |

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
