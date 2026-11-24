# 00 FinCrime Control Tower

One supervisor over the three starter projects, sharing one memory. A new regulation becomes policy, the policy
changes how KYC and transaction monitoring behave, and LangSmith measures the result.

```
Regulatory change (03, deep agent + sub-agents) --writes policy--+
KYC onboarding    (01, LangGraph)              --writes rating--+--> shared LangGraph Store
Txn monitoring    (02, agent + memory + HITL)  <--reads rating--+     (policy, customer_risk, alerts,
                                               --flags refresh--+      kyc_refresh, reviews, events)
Supervisor deep agent: regulatory-analyst sub-agent + tools for KYC, TM, refresh and reviews
```

The pillars never call each other. Each one still runs on its own from its own folder, so Nam, Marcus and
Alejandro can change their projects freely. `control_tower/contracts.py` defines what the supervisor needs from each
one, and `tests/test_contracts.py` checks it: pillar owners run
`uv run pytest -q 00_control_tower/tests/test_contracts.py` before opening a pull request. In short:

| Pillar | What the adapter needs from it |
|--------|--------------------------------|
| 01 KYC | `build_agent(**kwargs)`; state keys `profile`, `risk_level`, `risk_reasons`, `decision`, `application` |
| 02 TM | `build_agent(**kwargs)`; `utils.state.MyAgentContext`; decisions in the `("decisions", user_id)` store namespace |
| 03 Reg | `build_agent(**kwargs)`; the final message is the brief (any format) |

## Submission

LangChain asks for a Jupyter notebook and an `agents.py` in a forked repo:

- [`control_tower.ipynb`](control_tower.ipynb): the whole story, step by step, with outputs. Set
  `CONTROL_TOWER_OFFLINE=1` to run it without an API key.
- [`agents.py`](agents.py): exports `supervisor`, `kyc_agent`, `transaction_agent` and `regulatory_agent`.
  `langgraph.json` serves `supervisor` in Studio.

## Run it

From the repo root, once: `uv sync --all-packages` and fill in `.env`. Then from this folder:

```bash
uv run python -m control_tower.doctor          # checks .env, LangSmith login and one model call
uv run uvicorn portal.app:app --port 8000      # the demo portal: http://localhost:8000
uv run python scenario.py                      # the same story in the terminal
uv run langgraph dev                           # the supervisor in LangGraph Studio
uv run python -m evals.control_tower_evals     # datasets + experiments in LangSmith
uv run pytest -q tests                         # offline wiring tests
```

Add `CONTROL_TOWER_OFFLINE=1` (or `--offline` / `--local`) to run with a scripted model and no API key. Use it to
test the wiring and to rehearse. It replays the repo's analyst labels, so never present it as a model run; the
portal shows an "OFFLINE REHEARSAL" badge when it is on.

## The demo story (portal steps 1 to 6)

Tomás Ferreira (CUST-026, card USR-002) says in his application that his business partner is Viktor Harlan, a
PEP. Name screening misses that.

1. **Onboard** under today's rules: Tomás is LOW and auto-approved.
2. **Monitor**: TM uses default thresholds (human below confidence 0.7, grey zone 30–69).
3. **PS26-7 lands**: the 03 agent briefs it; the control tower proposes 8 control changes, each citing its
   paragraph; a reviewer approves them in the inbox.
4. **Re-screen**: Tomás is HIGH (4.1, 4.3) and held for EDD sign-off (4.6). The portal shows "was LOW under baseline".
5. **Monitor under PS26-7**: his thresholds tighten to 0.85 and 20–69 (6.3); two alerts in 30 days flag a KYC
   refresh (6.5).
6. **Process refresh**: the supervisor's loop re-opens his KYC file.

## LangSmith: tracing, evals, Engine

- **Tracing**: every pillar run traces to `LANGSMITH_PROJECT`. Run the supervisor in Studio to see all four agents
  in one trace.
- **Evals**: `evals/control_tower_evals.py` creates three datasets (policy extraction, KYC rating, end to end) with
  code evaluators: field accuracy, citation accuracy, rating correct, reasons cited, refresh loop fired, and a safety
  check that no high-risk customer is approved without a human.
- **Engine**: in LangSmith, open Engine, connect the tracing project (and the GitHub fork, if allowed) and let it
  cluster failures from the eval runs. Ask Avtar whether red teaming is on for the hackathon workspace.
- **Deployment (stretch)**: `langgraph.json` registers the supervisor, so it can go to LangSmith Deployment as is.

## Files

```
control_tower/
  loader.py        imports 01, 02 and 03 side by side (they all have top-level utils/tools/agent)
  memory.py        shared store namespaces, review queue, event log
  policy.py        PolicyParams, BASELINE, PS26_7_REFERENCE, LLM extraction, diff
  pillars/kyc.py   runs 01, applies the PS26-7 overlay (close associates, countries, source of funds)
  pillars/tm.py    runs 02 with rating-driven thresholds, merchant holds, alerts and the refresh trigger
  pillars/reg.py   runs 03, turns its brief into a policy proposal for sign-off
  reviews.py       one entry point for a human's answer to any parked case
  supervisor.py    the supervisor deep agent (exported as `agent` for langgraph dev)
  offline.py       scripted model for rehearsal and tests
portal/            FastAPI app + one HTML page
evals/             LangSmith datasets, targets and evaluators
scenario.py        the story from the command line
data/customer_links.jsonl   maps KYC customers (CUST-) to card accounts (USR-)
```

Added outside this folder: `CUST-026` and `CUST-027` in `01_kyc_agent/data/applications.jsonl`, and the synthetic
`PS26-7` in `03_document_summary_agent/data/documents/` (registered in `documents.jsonl`). All data is fictional.
