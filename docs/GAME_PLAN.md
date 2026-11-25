# MillTech FinCrime Control Tower: LangChain FSI Hackathon game plan

25 November 2026, GridIron, 8th floor, 1 Pancras Square · Team captain: Lukman Shoge

> The live version, with diagrams, is the team's Claude doc (ask Lukman for access). This file is the repo copy.

## The pitch

We build all three starter projects and join them into one system: a supervisor deep agent that runs KYC onboarding,
transaction monitoring and regulatory change as specialists sharing one memory of customer risk. Most teams will pick
one project. We show the three working as a single financial-crime function, measured end to end in LangSmith.

The demo story in one line: **a new regulation lands, the system turns it into policy, KYC and monitoring behaviour
changes, evals prove it, and LangSmith Engine catches what broke.**

Why it is credible: each person owns the pillar that matches their day job (Nam on KYC, Marcus on transaction
monitoring, Alejandro on regulatory change). Each pillar also stands on its own, so if the wiring slips we still submit
three working agents.

## Architecture

The supervisor is a deep agent that runs the three pillars: the regulatory deep agent as a sub-agent, KYC and TM
through tools. The pillars never call each other; they talk through a shared LangGraph Store, which keeps each one
runnable on its own.

```
                     Supervisor deep agent (Lukman)
          plans, routes to sub-agents, writes the final case file
              |                     |                       |
   Regulatory change          KYC onboarding          Transaction monitoring
   deep agent + parallel      LangGraph: screen,      agent with memory + HITL,
   section sub-agents         rate, human review      thresholds set by KYC rating
   (Alejandro, 03)            (Nam, 01)               (Marcus, 02)
              |                     |                       ^  |
   writes policy params      writes risk rating      reads rating, flags KYC refresh
              v                     v                       |  v
   ------------------- Shared memory (LangGraph Store) --------------------
   policy by firm · customer risk · alerts · KYC refresh flags · reviews
                                    |
   LangSmith: tracing, Studio, datasets and evals, annotation queues, Engine, Deployment
```

Regulatory change writes policy parameters (EDD triggers, thresholds) into the store. KYC reads them and writes each
customer's risk rating; TM reads that rating to set its thresholds and writes a refresh flag when alerts repeat, which
the supervisor routes back to KYC.

## Roles and briefs

Everyone owns one pillar end to end, including its eval. Lukman owns the glue and keeps unblocking the others.

| Person | Pillar | Starter | Builds | Owns in LangSmith |
| --- | --- | --- | --- | --- |
| Lukman | Supervisor and shared memory | `00_control_tower` | Supervisor deep agent, shared store, cross-pillar wiring, end-to-end eval, Engine | Tracing project, end-to-end dataset, Engine issues |
| Alejandro | Regulatory change | 03 compliance brief | His Version B, plus PS26-7, the fincrime policy that drives KYC and TM | Gold set; quote, deadline, recall, precision and applicability evaluators |
| Nam | KYC onboarding | 01 KYC agent | Risk rating that other pillars read; EDD rules from the policy | KYC gold labels for the 27 applications, annotation queue |
| Marcus | Transaction monitoring | 02 TM agent | KYC rating as a prior on thresholds; repeat alerts trigger KYC refresh | Scoring evals (Module 2), annotation queue (Module 3) |

### Alejandro: regulatory change

Alejandro's Version B plan is the regulatory pillar as he wrote it: Horizon Scanner format, per-firm applicability for
FIRM-001 to FIRM-003, code-computed deadlines, re-run on failed checks, reviewer sign-off and the five evaluators. Build
order stays his: starter end to end by 11:30, then gold set and evals, then the rest.

One addition links his pillar to the others: run the same pipeline on a second synthetic document, PS26-7 (financial
crime controls), drafted for him to refine. Its obligations carry machine-readable parameters (EDD triggers, review
thresholds), which the supervisor writes into the shared store as policy for KYC and TM to read.

### Nam: KYC onboarding

1. Get 01 running in Studio by 11:00 (CUST-001 approve, CUST-002 interrupt).
2. Change `assess_risk` from "any hit = high" to low / medium / high with reasons, reading EDD rules from the shared
   policy (PEP, high-risk country, source of funds).
3. Write the final rating to the shared store under the customer's ID.
4. Hand-label the 27 applications (expected rating, expected decision). That is the KYC gold set and Nam's main
   expert contribution.

### Marcus: transaction monitoring

1. Run Module 1 of 02 by 11:00, then Module 2 (scoring evals).
2. Read the customer's KYC rating from the shared store in `record_decision`. High-risk customers get a lower
   confidence threshold and a wider grey zone, taken from the policy's parameters.
3. Two or more blocks or escalations for one customer write a `kyc_refresh` flag, which the supervisor routes back to KYC.
4. Review runs in the Module 3 annotation queue. Marcus's labels are the TM gold set.

### Lukman: supervisor and glue

1. Shared store module and the supervisor deep agent, with the regulatory deep agent as a sub-agent and KYC and TM as
   tools that park human reviews in a shared queue.
2. One tracing project so a single run shows all four agents.
3. End-to-end dataset, then LangSmith Engine on the project, red-teaming included if it is enabled.
4. Stay available: unblock setup, keys and merge conflicts first.

## Timeline for the day

The morning is for standalone pillars and the afternoon for wiring. Registration and breakfast run 08:15 to 09:00, then
LangChain's welcome, LangSmith walkthrough and setup check until 10:00. Alejandro (08:15) and Nam (08:30) check laptops
early; Lukman arrives about 09:30. Demos run 16:00 to 17:00, winners at 17:00.

| Checkpoint | Gate |
| --- | --- |
| 11:30 | Every starter runs end to end |
| 13:00 | Each pillar works standalone and writes to the shared store |
| 15:00 | Wiring freeze: the end-to-end run is green |
| 15:30 | Demo rehearsal |
| 16:00 | Demos (5 minutes plus Q&A) |

| Person | Build block 1 (10:00 to 13:00) | Build block 2 (14:00 to 16:00) | Demo |
| --- | --- | --- | --- |
| Lukman | Store and supervisor; unblock everyone's setup first | Wire pillars, end-to-end eval, Engine | Trace, Engine |
| Alejandro | Starter 03 end to end by 11:30, then gold set and evaluators | PS26-7 run writes policy; A to E brief, human review | Open, close |
| Nam | Starter 01 in Studio; low, medium, high rating with EDD rules | Rating to store; label the applications as a gold set | KYC step |
| Marcus | Starter 02 Modules 1 and 2; baseline scoring experiment | KYC rating sets thresholds; refresh flag, Module 3 queue | TM step |

Fallback: if a pillar is not standalone at 13:00, its owner keeps polishing it and Lukman integrates the other two. If
the end-to-end run is not green at 15:00, we demo the pillars separately and show the shared store and one combined
trace as the direction.

## How we score

Judging is out of 30 across relevance and impact, technical complexity, and the 5-minute presentation.

| Criterion | What judges look for | What we show |
| --- | --- | --- |
| Relevance and impact | A real FSI problem, clear user, measurable value | Regulation to controls is how a compliance function works, built by the people who do the job |
| Technical: multi-agent | Sub-agents, orchestration | A supervisor deep agent over a LangGraph graph, a LangChain agent and a deep agent with parallel sub-agents |
| Technical: memory | Short- and long-term memory | A shared store across agents: KYC rating feeds TM, TM alerts re-open KYC, reviewer notes feed later decisions |
| Technical: evals | LangSmith datasets and experiments | KYC, TM, regulatory gold set and end-to-end datasets, annotation queues, a before/after experiment |
| Presentation | A clear story in 5 minutes, standard slide template, Q&A | One scenario through every pillar in one trace, then Engine finding a failure |

LangChain's verticals all appear: LangChain and LangGraph (build), Deep Agents (harness), LangSmith (tracing, evals,
annotation queues, Studio), Engine (issue detection and fixes) and Deployment (`langgraph dev` today; LangSmith
Deployment as the stretch goal).

## Five-minute demo script

One customer, Tomás Ferreira, goes through every pillar. His synthetic KYC application (added by us) says he is the
business partner of Viktor Harlan, a PEP on the list. Name screening alone does not catch that. We drive the demo from
the Control Tower portal: six story buttons, a review inbox, and the live policy with paragraph citations.

1. **0:00 The problem (Alejandro).** Compliance runs KYC, monitoring and regulatory change in silos. A new rule takes
   weeks to reach the controls.
2. **0:45 Regulation lands (Alejandro).** The supervisor briefs PS26-7 for our firm. Sub-agents run in parallel and code
   checks every quote and deadline. Paragraphs 4.1 and 4.3 add EDD for close associates of PEPs; 6.3 tightens review
   for high-risk customers; 6.5 links alerts back to KYC. Reviewer approves.
3. **1:45 Policy changes behaviour (Nam).** Re-screen Tomás: before PS26-7 he was low risk; now he is high risk with
   the reason cited to paragraphs 4.1 and 4.3, and held for EDD sign-off (4.6).
4. **2:30 Monitoring adapts (Marcus).** His thresholds tighten from confidence 0.7 and grey zone 30–69 to 0.85 and
   20–69 (6.3). The impossible-travel block (TX-2004) and the flight referral (TX-2005) are two alerts in 30 days, so
   paragraph 6.5 flags his KYC file for refresh.
5. **3:15 One trace (Lukman).** The LangSmith trace: supervisor, KYC graph, TM agent and regulatory sub-agents in one
   tree, with cost and latency.
6. **3:45 Evals and Engine (Lukman).** Before/after experiment scores. Engine shows an issue it clustered from our
   failed runs and the fix it proposes.
7. **4:30 Close (Alejandro).** Every decision is traceable to a paragraph, every change is measured, and a human signs
   off where it matters.

## Submission and setup rules

From LangChain's attendee guide: we submit **a Jupyter notebook and an agents.py in a forked GitHub repo**. Ours are
[`00_control_tower/control_tower.ipynb`](../00_control_tower/control_tower.ipynb) and
[`00_control_tower/agents.py`](../00_control_tower/agents.py) in this repo, a public fork. The slide template and full
submission guidance arrive on the day.

| Rule | What it means for us |
| --- | --- |
| Synthetic or public data only | Repo data plus PS26-7 and two added applicants, all fictional |
| Keep `.env` and API keys out of git | `.env` is in `.gitignore`; never paste keys in chat |
| Outputs are not legal, compliance or regulatory advice | Every brief and rating is a draft for a human reviewer |
| LangSmith: sign in with the registered email, not SSO | The hackathon org is hosted in the GCP US region |
| Setup check | Run the KYC notebook's first cells and confirm a trace appears in LangSmith |
| Windows laptops | Use PowerShell or WSL; install Visual Studio C++ Build Tools and the 2015 C++ runtime |
| Network allowlist | github.com, smith.langchain.com, api.smith.langchain.com, gateway.smith.langchain.com, pypi.org, astral.sh |

Fergus also recommends [langchain-skills](https://github.com/langchain-ai/langchain-skills) while building: LangChain's
skills for coding agents.

## Tonight's checklist

- [x] Lukman: fork to milltechfx/LangchainHackathon and push the Control Tower, PS26-7, process maps and CONTRIBUTING.md
- [x] Lukman: add the submission notebook and agents.py (`00_control_tower`)
- [ ] Lukman: merge the control-tower pull request and send the team message with the repo link
- [ ] Lukman: share the plan with Alejandro, Nam and Marcus
- [ ] Lukman: reply to Fergus; confirm with Avtar that we all trace into one LangSmith workspace and whether Engine is on
- [ ] Everyone: accept the LangSmith invite (email, not SSO), clone the repo, run `uv sync --all-packages` (add
      `--python 3.13` if it fails), fill `.env`, and see one KYC trace in LangSmith
- [ ] Everyone: check your page in [docs/process_maps](process_maps/README.md) (inputs, outputs, tools) and add your ideas
- [ ] Alejandro: review PS26-7 and add his process-map PDF next to his page
- [ ] Nam: note the rating you would give each of the 27 KYC applications
- [ ] Marcus: note the thresholds you would set for high-risk customers

Ground rules: synthetic data only, personal laptops, no MillTech systems or client data. Every output is a draft for a
human reviewer, not a decision.

## What is already built

| Piece | What it does |
| --- | --- |
| Supervisor deep agent | The 03 agent as a `regulatory-analyst` sub-agent, plus tools for KYC, TM, refresh and reviews. Runs in Studio |
| Pillar adapters | Run 01, 02 and 03 unchanged, side by side, and connect them to the shared store |
| PS26-7 | Synthetic fincrime policy for Alejandro to refine; 8 control changes, each with its paragraph |
| Portal | FastAPI and one HTML page: story steps, customer cards, review inbox, live policy, activity feed |
| Evals | Three LangSmith datasets with code evaluators, including a safety check that no high-risk customer is approved without a human |
| Offline mode | Scripted model for rehearsal with no API key; the portal shows an OFFLINE REHEARSAL badge |
| Submission | `control_tower.ipynb` and `agents.py`, both run end to end offline |

Still to do on the day: run against the gateway models, extend the KYC gold set to all 27 applications (Nam), connect
Engine, and confirm the 03 brief yields a good policy extraction.
