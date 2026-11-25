# Agent guide: MillTech FinCrime Control Tower

Read this before changing anything. It is the hand-off for coding agents (Claude Code, Copilot, Cursor) working in
this repo. Humans: start with README.md, docs/GAME_PLAN.md and CONTRIBUTING.md.

## What this is

MillTech's team repo for the LangChain FSI Hackathon (London, Wed 25 Nov 2026, demos 16:00). It is a fork of
`langchain-samples/fsi-hackathon-london-2026` at `milltechfx/LangchainHackathon` (public). All data is synthetic.

Four agents, one shared memory:

| Folder | Agent | Built with | Owner |
|---|---|---|---|
| `00_control_tower/` | Supervisor + portal + evals + submission | Deep Agents (`create_deep_agent`), FastAPI | Lukman (senior dev, team captain) |
| `01_kyc_agent/` | KYC onboarding | LangGraph `StateGraph`, `interrupt()` for human review | Nam (KYC compliance, not a developer) |
| `02_transaction_monitoring_agent/` | Card transaction monitoring | LangChain `create_agent`, checkpointer + store, `interrupt()` | Marcus (risk manager, not a developer) |
| `03_document_summary_agent/` | Regulatory change / compliance brief | Deep agent with parallel `section-summariser` sub-agents | Alejandro (compliance, not a developer) |

The demo story: synthetic regulation **PS26-7** (`03_document_summary_agent/data/documents/PS26-7.md`) is briefed by
03; the supervisor turns the brief into `PolicyParams` (control changes, each citing a paragraph); a human approves;
KYC re-rates **Tomás Ferreira (CUST-026, card USR-002)** from LOW to HIGH because his application names a PEP
(Viktor Harlan) as business partner (paras 4.1, 4.3); TM tightens his thresholds to confidence 0.85 and grey zone
20–69 (6.3); two alerts in 30 days flag a KYC refresh (6.5).

## Architecture (00_control_tower)

```
control_tower/
  loader.py      load_project(folder): imports 01/02/03 side by side. They all have top-level `utils`, `tools`,
                 `agent` modules, so each is imported with its folder first on sys.path, then its modules are
                 removed from sys.modules. Pillar code must import at module top level (no lazy imports).
  contracts.py   THE CONTRACT: what the supervisor needs from each pillar (KycOutput, TmDecision,
                 TmContextFields, reg_request, brief_from_result, read_policy). Change carefully.
  memory.py      Shared LangGraph Store namespaces: ("policy", firm), ("customer_risk",), ("alerts", user),
                 ("kyc_refresh",), ("reviews",), ("events",), ("client",). Also CUST-<->USR- mapping
                 (data/customer_links.jsonl). ("client",) is the client file: lifecycle stage (onboarding, active,
                 reassessment_due, under_review, refresh_due), policy last assessed under, dated history. Only the
                 adapters write it (record_client); the supervisor reads it. Approving a policy marks clients
                 assessed under the old one (mark_policy_change); nothing is re-run without a human asking.
  policy.py      PolicyParams, BASELINE (repo defaults), PS26_7_REFERENCE (gold answer), extract_policy (LLM), diff.
  pillars/kyc.py runs 01 graph, validates KycOutput, adds the PS26-7 overlay (close associates, high-risk
                 countries, source of funds), writes the rating, parks reviews (EDD sign-off, graph interrupts).
  pillars/tm.py  runs 02 agent with MyAgentContext set from the KYC rating; merchant holds (6.4); alerts; refresh
                 trigger (6.5); parks interrupts as review cases and resumes them.
  pillars/reg.py runs 03 agent, extracts policy from the brief, parks it for approval (four-eyes).
  reviews.py     answer_review(store, case_id, answer): one entry point for any human decision.
  supervisor.py  the supervisor deep agent. It works from skills (agent_home/skills/*/SKILL.md, read-only to the
                 agent), one per lifecycle stage: onboard-client, daily-monitoring, apply-regulatory-change,
                 kyc-refresh, client-briefing. All three pillars are tools (no sub-agents). It has no tool to answer
                 a review. Tools use runtime.store (langgraph dev) or memory.get_store().
  offline.py     ScriptedModel: replays analyst labels/fixtures so everything runs with no API key.
  doctor.py      checks .env, LangSmith login and one gateway model call. Never prints keys.
  settings.py    MODEL from MODEL_NAME in the root .env; illustrative FX rates to GBP.
agents.py               SUBMISSION: exports supervisor, kyc_agent, transaction_agent, regulatory_agent.
control_tower.ipynb     SUBMISSION: the story step by step. Commit it WITHOUT outputs.
portal/app.py + static/index.html   demo UI, four tabs. Dashboard: counts, Scan (screens the next new applications,
                        monitors onboarded card clients), and one action queue for every human decision from every
                        pillar (approve/reject, POST /api/action/{id}), plus a chat box to the supervisor (live only).
                        KYC (portal/kyc_tab.py + static/kyc.js/.css): the KYC process map with a count per stage,
                        a filterable case list, and a case file that walks each step of the map (application,
                        profile, screening, risk with paragraphs, approval, monitoring); watchlists folded away.
                        Bulk: "Screen all new" and "Re-assess all due" run one background job; decisions still land
                        in the action queue. Offline covers all 27 applicants (data/offline_applicants.jsonl). Transaction monitoring: card clients,
                        decisions next to analyst labels. Regulatory change: internal (firm, providers, brief) and
                        clients (control changes, who is affected).
scenario.py             the story from the CLI.
evals/control_tower_evals.py        LangSmith datasets + code evaluators (policy extraction, KYC rating, end to end).
tests/                  conftest.py enables offline mode for all tests; test_contracts.py is what pillar owners run.
langgraph.json          serves agents.py:supervisor for `langgraph dev`.
```

## Commands

Run from `00_control_tower/` unless noted. The venv is `.venv` at the repo root, created by `uv sync --all-packages`
(repo root). Python is pinned to 3.13 in `.python-version` (3.15 has no orjson wheel).

```bash
uv run python -m control_tower.doctor                  # live setup check (needs .env)
uv run pytest -q tests                                 # 27 offline tests; must stay green
uv run python scenario.py --offline                    # whole story, scripted model
uv run python scenario.py                              # whole story, real models, traced to LangSmith
CONTROL_TOWER_OFFLINE=1 uv run uvicorn portal.app:app --port 8000   # rehearsal portal
uv run uvicorn portal.app:app --port 8000              # live portal: http://localhost:8000
uv run langgraph dev                                   # supervisor in LangGraph Studio
uv run python -m evals.control_tower_evals --local     # evals offline; drop --local to run in LangSmith
```

Never run plain `uv sync` inside a project folder: it trims the shared env. Use `uv sync --all-packages` from root.

## Rules

- Never commit or print `.env` or API keys. Never paste keys into chat.
- Synthetic data only. No MillTech or client data, no connections to company systems.
- Keep each pillar runnable on its own from its own folder. Do not make 01/02/03 import each other.
- Don't edit 01/02/03 internals to suit the supervisor: change the adapter in `pillars/` or the contract instead, and
  tell the owner. Owners may change their agents at any time; `tests/test_contracts.py` must still pass.
- Offline mode shows plumbing, not model quality. Never present offline output as a model result; the portal badge
  must stay.
- Outputs are drafts for a human reviewer, not compliance advice. Never auto-approve on a human's behalf.
- Work on a branch, open a PR into `main`, run `uv run pytest -q 00_control_tower/tests` first.
- Commit messages end with the session attribution lines if your tool adds them; otherwise plain messages are fine.

## State at hand-off (Tue 24 Nov, evening)

Done and tested offline: everything listed above; 17 tests pass; notebook and portal run offline.

Not yet done:
1. **Live run.** `.env` not yet filled in on Lukman's laptop. Next: fill `.env`, run the doctor, then
   `python scenario.py` live and fix what breaks. Most likely breakage: `extract_policy` (LLM structured output into
   `PolicyParams`) and the 02 agent's real confidence scores vs the thresholds.
2. **Policy extraction quality.** Compare the live `PolicyParams` from the 03 brief with `PS26_7_REFERENCE`
   (`evals --suite policy-extraction`). If recall is poor, pass the document text alongside the brief.
3. **Supervisor end to end in Studio** (`langgraph dev`): check the single trace shows all four agents.
4. **LangSmith Engine**: connect it to the tracing project after the eval runs (UI step; confirm it is enabled for
   the hackathon workspace with Avtar/Fergus).
5. **Teammates' changes on the day**: merge their PRs, run the contract tests, adjust adapters.
6. Run `control_tower.ipynb` live just before submitting, then clear outputs only if they are offline outputs.

## Known gotchas

- `uv sync` building orjson from source = wrong Python. Use 3.13 (`.python-version`).
- Two clones existed on Lukman's laptop: use `C:\Users\lshoge\code\LangchainHackathon` (WSL path
  `/mnt/c/Users/lshoge/code/LangchainHackathon`). The older clone in the WSL home folder is stale; delete it.
- The 02 agent keeps a conversation thread per customer; `run_tm(..., run_tag=...)` separates before/after runs.
- MDA (Managed Deep Agents) is a stretch goal only; `langgraph.json` already works for LangSmith Deployment.
