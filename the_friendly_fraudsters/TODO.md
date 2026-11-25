# The Friendly Fraudsters: team to-do list

FSI Hackathon London, 25 Nov 2026. Tick items off (`[x]`) as you finish them and push, so everyone can see progress.

## Key times

| Time | What |
| --- | --- |
| 10:00–13:00 | Build block 1 |
| **Before lunch** | Quality reviewer and customer letter agents merged into `main` |
| 13:00–14:00 | Lunch |
| 14:00–15:45 | Build block 2 |
| **15:45** | **Submission deadline:** fork link and slides |
| 16:00 | Demos: 5 minutes plus Q&A |

## How we're judged (30 points)

| Area | What judges look for | Who covers it |
| --- | --- | --- |
| Use case impact | A real FSI problem with a clear user. **KPIs and ROI estimated** | Asia (numbers), Wasiu (slides) |
| Technical depth | LangGraph, memory, middleware, **evals**; completeness, system design, architecture | Everyone |
| Presentation | 5-minute demo, on time, **works end to end**, handles Q&A | Wasiu leads, everyone rehearses |

## Submission checklist (everyone)

- [ ] Agree our **company name**. Format: `The Friendly Fraudsters | <Company>`
- [ ] README has team name, company, use case and how to run it
- [ ] `dispute_agent.ipynb` (the notebook) and `agent.py` committed to `main`
- [ ] Evals and datasets committed (`data/`, `evals/`)
- [ ] 5-minute deck from the LangChain template, named `HACK_COMPANYNAME_TEAMNAME`
- [ ] Fork link and slides submitted via the QR code / link in the master deck, and emailed to both hosts **by 15:45**

## Shared decisions (agree in the first 15 minutes)

- [ ] **New field names in `utils/state.py`**, so everyone codes against the same names. Proposed:
  - `vulnerability`: `{"level": "none|low|medium|high", "types": [...], "reasons": [...]}`
  - `review_result` (`pass` / `fail`), `review_feedback`, `send_back_to`, `retry_count`
  - `customer_letter`, `letter_type`
- [ ] **What "good enough" means for an evidence pack** (Pradeep and Mariam)
- [ ] **ROI assumptions** (see the KPIs and ROI section): manual minutes per case, review minutes with the agent, analyst cost per hour, cases per year

---

## Asia: Task 1, data and evals

Branch: `data-evals`

| # | Task | When | Status |
| --- | --- | --- | --- |
| 1 | Dataset v1: 12 labelled cases (`data/complaints.jsonl` + linked customers, payments, history) | Morning | Done |
| 2 | Data checker: `uv run python -m evals.check_data` | Morning | Done |
| 3 | Review the 12 labels | Morning | Done |
| 4 | Quality reviewer test set for Pradeep: 8 evidence packs (`data/qa_test_packs.jsonl`) | Before lunch | Done |
| 5 | Letter test set for Wasiu: 8 scenarios with must / must-not rules (`data/letter_test_cases.jsonl`) | Before lunch | Done |
| 6 | Baseline eval: reason code 12/12, route 11/12, vulnerability 12/12, high-risk caught 0/1 | Before lunch | Done |
| 7 | Notebook sections 1–3 | Before lunch | Done |
| 8 | Commit, push and open a pull request so the team gets the data | Before lunch | To do |
| 9 | Grow the dataset to 25 cases: harder, vaguer complaints, mixed problems, some **low** vulnerability cases (then bump `DATASET` to v2 in `evals/run_evals.py`) | 14:00 | To do |
| 10 | Full eval on the merged agent, with time and cost per case | 14:30 | To do |
| 11 | Score the quality reviewer (`score_reviewer`) and the letters (`check_letter`) | 14:30 | To do |
| 12 | Find failures, fix a prompt with Pradeep, re-run: the "after" score | 14:45 | To do |
| 13 | Notebook sections 4–8: end-to-end run, results table, KPIs and ROI, next steps | 15:15 | To do |
| 14 | Hand the results and ROI numbers to Wasiu for the slides | 15:30 | To do |
| 15 | Final commit and push | Before 15:45 | To do |

## Pradeep: quality review agent, then Task 2, graph and routing

**Before lunch: the quality review agent**

- [ ] Write `quality_review` in `utils/nodes.py`. It reads the evidence pack and returns `review_result`, `review_feedback` and `send_back_to`
- [ ] Checklist: every claim backed by the data, nothing made up, recommendation matches the evidence, all sections present, vulnerability handled, no accusing tone, reason code fits the complaint
- [ ] Send-back targets: write-up problems → `draft_evidence_pack`; bad reasoning → the specialist; wrong reason code → `classify_dispute`; missing evidence or 2 failed retries → human
- [ ] Test it: `from evals.test_sets import score_reviewer`. Target: 8/8 verdicts, 8/8 send-back steps
- [ ] Add the new fields to `utils/state.py` (you own this file)
- [ ] Merge into `main` before lunch

**After lunch: Task 2, graph and routing**

- [ ] Wire the review loop in `agent.py`: fail → send back with feedback, at most 2 retries, then to the human
- [ ] Wire `vulnerability_check` straight after `load_case`, with the **high-risk fast lane** to a human (fixes the baseline's 0/1)
- [ ] Wire `customer_letter` after `human_approval`
- [ ] Keep `main` running end to end; merge teammates' pull requests
- [ ] Improve the classifier and triage rules with Asia, using the eval failures

## Wasiu: customer letter agent, then Task 4, memory, approval and demo

**Before lunch: the customer letter agent**

- [ ] Write `customer_letter` in `utils/nodes.py`. It picks the letter type from the outcome: refund / chargeback, decline, friendly-fraud decline, needs more info, fast-lane holding message
- [ ] Rules: plain English; declines mention the Financial Ombudsman Service; friendly-fraud letters never accuse; vulnerable customers get simpler words and a support contact; if a human overrode the AI, follow the human's decision
- [ ] Test it: `from evals.test_sets import letter_cases, check_letter`. Target: every letter passes
- [ ] Write `vulnerability_check`: reads the complaint, profile and history; returns level, types and reasons. High risk → fast lane
- [ ] Merge into `main` before lunch

**After lunch: Task 4, memory, approval and demo**

- [ ] Human approval screen: vulnerability shown at the top; approve, override or request more info
- [ ] Memory visible on repeat disputes ("this customer has lost 2 earlier fraud claims")
- [ ] **Slides** (5 minutes, from the LangChain template): problem and who it helps → how it works → live demo → results (eval scores, KPIs, ROI) → how we built it
- [ ] Demo script with 2–3 cases: the sofa (vulnerable, normal flow), a friendly-fraud decline, the crisis fast lane
- [ ] Name the slides `HACK_COMPANYNAME_TEAMNAME`, rehearse to under 5 minutes

## Mariam: Task 3, specialists and evidence pack

- [ ] **Fraud specialist:** country vs home, unknown device, failed 3-D Secure, odd hour
- [ ] **Merchant specialist:** cancellation dates, delivery windows, duplicate amounts
- [ ] **Friendly-fraud specialist:** fair and careful, never accusing
- [ ] Use the vulnerability flag: consider refunding while investigating if the customer can't afford the loss
- [ ] Evidence pack sections: Summary, Reason code, Transaction details, Evidence, Customer history, **Vulnerability**, Recommendation (the quality reviewer checks these)
- [ ] When the reviewer sends a pack back, rewrite it using `review_feedback`
- [ ] Agree with Pradeep what "good enough" means
- [ ] Test: run the sample complaints and check the packs against `data/qa_test_packs.jsonl` (QA-01 and QA-02 are examples of good packs)

---

## The notebook (`dispute_agent.ipynb`)

The judges' proof that the agent works: they can open it on GitHub and see the outputs without running anything.

| Section | What it shows | Status |
| --- | --- | --- |
| 1. The problem | Disputes, and who it helps (the disputes team) | Done |
| 2. Setup | Loads the data and `.env` | Done |
| 3. The dataset | Table of cases, data checks, both test sets | Done (grow to 25 after lunch) |
| 4. One case end to end | The sofa case: every step, evidence pack, human pause, approval, letter | After merge |
| 5. Evals | Runs the LangSmith eval and shows the scores (baseline already noted) | After merge |
| 6. Results table | Like the KYC example: correct out of 25, missed high-risk, false alarms, time per case. Every miss explained | After merge |
| 7. KPIs and ROI | The calculation below | After merge |
| 8. Next steps | What we'd improve | After merge |

## KPIs and ROI

**Quality KPIs** (measured by the evals):

- Reason code correct: X / 25
- Route correct: X / 25
- **Missed high-risk vulnerable customers: must be 0**
- False vulnerability alarms
- Planted mistakes caught by the quality reviewer: X / 6
- Letters passing every rule: X / 8

**Speed and cost KPIs** (measured from LangSmith traces): time per case, tokens and cost per case.

**ROI:**

```
Saving per case = (manual minutes − human review minutes with agent) × analyst cost per minute − AI cost per case
Annual saving   = saving per case × cases per year
```

| Number | Where it comes from |
| --- | --- |
| AI time and cost per case | **Measured** in the evals |
| Human review minutes with the agent | **Assumption.** A human still approves every case: this is the time to read the evidence pack |
| Manual minutes per case | **Assumption**, or a public figure if we find a source |
| Analyst cost per hour | **Assumption** |
| Cases per year | **Assumption**, e.g. a mid-sized UK bank |

Label every assumption clearly on the slide. The saving comes from **faster investigation, not removing the human**: that's honest, and a good answer for Q&A.
