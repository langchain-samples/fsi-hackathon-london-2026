# Document Summary Workshop

A hands-on tutorial for learning deep agents by building one that summarises a long regulatory document into a
brief for a specific firm. It uses two agents:

1. A **main agent** loads the document, which arrives already split into sections, and looks up the firm's profile.
2. It hands each section to a **sub-agent**, `section-summariser`, which summarises that one section and extracts
   its obligations as a structured `SectionSummary`. The sub-agents run in parallel.
3. The main agent collects the summaries and writes a brief for the firm.
4. Plain code checks every obligation's quote and deadline against the source text.

**Time:** about 30 minutes. **Level:** beginner to intermediate. The regulator, the firm and the document are
fictional and offline, stored in `data/`; the only network call is to the model.

The project uses the same layout as the
[Managed Deep Agents (MDA) project structure](https://docs.langchain.com/langsmith/python/managed-deep-agents-project-structure):
`agent.py` exports `agent`, and the code that reads data lives in `tools/`. The agent is built with
`create_deep_agent` and deployed with LangGraph (`langgraph.json`).

If you'd like to go into more depth, or work through tutorials on your own, check out
[LangChain Academy](https://academy.langchain.com).

## Setup

Set up the shared Python environment and `.env` file first, by following the
[Setup](../README.md#setup) and [Environment variables](../README.md#environment-variables) sections of the
[top level README](../README.md). All three topics use that one environment.

Then, from this folder, start Jupyter:

```bash
uv run jupyter notebook
```

In VS Code, pick the interpreter at `.venv/bin/python` in the repo root as the notebook kernel.

Don't run a plain `uv sync` inside this folder: it trims the shared environment down to this topic's packages and
breaks the other two. If that happens, run `uv sync --all-packages` from the repo root again. `uv run` and `uv add`
are fine to use here.

Open `modules/01_document_summary_agent.ipynb` and run each cell in order.

| Key | Required for | Get one |
|-----|--------------|---------|
| `LANGSMITH_API_KEY` | Tracing | https://smith.langchain.com |
| `MODEL_NAME` | Module 1 (optional) | `provider:model` to choose the model. Defaults to `openai:gpt-6-luna`. |

## Switching models

All modules import `model` from `utils/models.py`. It reaches the model through the
[LangSmith LLM Gateway](https://docs.langchain.com/langsmith/llm-gateway-direct-model-access) set up in `.env`, and
uses `openai:gpt-6-luna` by default. You don't need to edit any code or notebooks.

To choose the model, set `MODEL_NAME` as `provider:model` in `.env`, for example:

```bash
# .env
MODEL_NAME=anthropic:claude-sonnet-5-5
```

OpenAI and Anthropic work straight away. For other providers, install their package in this folder first, for
example `uv add langchain-aws` for AWS Bedrock (`bedrock_converse:<model ID>`). Azure OpenAI
(`azure_openai:<deployment>`) works the same way, with its own credentials.

## The data

The firm's data is a set of JSONL files in `data/`: one file per table, one JSON object per line. `utils/db.py`
reads them like a tiny read-only database (`db.table`, `db.query` and `db.get`).

| Table | What's in it | Read by |
|-------|--------------|---------|
| `documents` | The document library: one row per document, with the Markdown file that holds its text | `tools/documents.py` |
| `firms` | The firms we write briefs for, and which providers each one uses, with contract end dates | `tools/firms.py` |
| `third_party_providers` | The providers' names and the services they provide | `tools/third_party_providers.py` |

The agent's input is a request such as "Summarise document PS26-4 for FIRM-001", and its tools look up the rest.
To brief on another document, put its Markdown in `data/documents/` and add a row for it to `data/documents.jsonl`.

## Run the agent

`agent.py` exports the main agent as `agent`. To chat with it in LangGraph Studio, run this from this folder:

```bash
uv run langgraph dev
```

Then send it a message such as `Summarise document PS26-4 for FIRM-001`. Or call it from Python:

```python
from agent import agent

result = agent.invoke({"messages": [{"role": "user", "content": "Summarise document PS26-4 for FIRM-001"}]})
print(result["messages"][-1].text)
```

Because `agent.py` imports `model` from `utils.models`, the model your `.env` selects is the one the agent uses
everywhere: in the notebook, in Studio, and when deployed.

## Project structure

```
03_document_summary_agent/
├── README.md                       (this file: setup and how to run)
├── pyproject.toml                  (dependencies)
├── langgraph.json                  (registers agent.py's `agent` for langgraph dev)
├── agent.py                        (the main deep agent, exported as `agent`)
├── instructions.md                 (the main agent's system prompt)
├── sub_agent.py                    (the section-summariser sub-agent and its SectionSummary output)
├── sub_agent_instructions.md       (the sub-agent's system prompt)
├── tools/
│   ├── documents.py                (get_document)
│   ├── firms.py                    (get_firm_profile)
│   └── third_party_providers.py    (get_third_party_provider)
├── data/                           (the firm's database: one JSONL file per table, plus the documents)
│   ├── documents.jsonl
│   ├── firms.jsonl
│   ├── third_party_providers.jsonl
│   └── documents/
│       └── PS26-4.md               (the fictional regulatory document)
├── utils/
│   ├── db.py                       (reads the JSONL tables in data/)
│   └── models.py                   (the model every module uses)
└── modules/
    └── 01_document_summary_agent.ipynb   (Module 1)
```

## Common issues

**`ModuleNotFoundError` for a package, such as `langchain_anthropic`.** The notebook is running on the wrong
Python. Pick `.venv/bin/python` in the repo root. Check with
`import sys; print(sys.executable)`. If the kernel is right, run `uv sync --all-packages` from the repo root.

**The notebook can't find `agent`, `utils` or `tools`.** The notebook's setup cell adds the workshop root to
`sys.path`. If you moved the notebook, update the `Path().resolve().parent` line so it points at the
`03_document_summary_agent` folder.

**`KeyError: 'LANGSMITH_API_KEY'` or `'LC_GATEWAY_KEY'` (or another key).** Your `.env` file is missing or doesn't set that key. Check you
copied `.env.example` to `.env` in the repo root (one level above the `03_document_summary_agent` folder) and
filled it in.

**`No module named 'utils'` when importing `agent`.** Run Python from the workshop root (this folder), for example
`uv run python -c "import agent"`.

**The sub-agents return different fields.** Each sub-agent must return a `SectionSummary`. If a section's output has
other keys, check that `sub_agent.py` still passes `response_format=SectionSummary`.

**`403 Forbidden` errors from LangSmith.** Tracing can't reach LangSmith because `LANGSMITH_API_KEY` in `.env` is
wrong. The agent still runs; set `LANGSMITH_TRACING=false` to turn tracing off.
