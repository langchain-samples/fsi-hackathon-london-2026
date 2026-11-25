# The Friendly Fraudsters: Card Dispute Agent

Team plan, 24 Nov 2026

## Getting started

```bash
git clone https://github.com/asiasharif/fsi-hackathon-london-2026.git
cd fsi-hackathon-london-2026
uv sync --all-packages
cp .env.example .env              # add your own keys; never commit .env
cd the_friendly_fraudsters
git checkout -b <your-role>       # e.g. data-evals, graph-routing, specialists, memory-demo
uv run langgraph dev              # opens LangSmith Studio; input {"complaint_id": "CMP-001"}
uv run python -m evals.run_evals  # scores the agent in LangSmith
```

## How we use git

`main` is the shared copy and must always run. Nobody works on `main` directly: each person works on their own branch and merges in through a pull request.

**Start of the day:**
```bash
git checkout main
git pull origin main                    # get everyone's latest work
git checkout -b <your-role>             # data-evals, graph-routing, specialists or memory-demo
```

**While you work (commit and push often):**
```bash
git add the_friendly_fraudsters
git commit -m "Short description of what you did"
git push origin <your-role>
git pull origin main                    # now and then, to pick up teammates' merged work
```

**To get your work into `main`:** on GitHub, open a pull request from your branch into `main`. A teammate checks it and clicks **Merge**. Merge small pieces often (before lunch, mid-afternoon), not everything at the end.

**Watch out:**
- **Check where your pull request points.** This repo is a fork, so GitHub defaults to LangChain's original repo. At the top of the pull request page, set the base repository to `asiasharif/fsi-hackathon-london-2026` and the base branch to `main`.
- **`utils/nodes.py` is shared.** Edit only your own functions in it.
- **`utils/state.py` is shared too.** Change it only after agreeing with the graph and routing person.
- **Never commit `.env`.** It holds your keys, and git already ignores it.
- **Pushing asks for a password?** Use a GitHub personal access token, not your account password.

## Overview

We are building a team of AI agents that handles card disputes for a bank. A customer says "I didn't make this payment", and the agents work out what kind of problem it is, check the evidence, recommend what to do and write the case up. A quality reviewer checks the write-up, a member of staff approves the decision, and the customer gets a clear letter. A working starter is in this folder (`the_friendly_fraudsters/`), and the 4 of us each take one part and improve it during the day.

## The problem

Banks get thousands of complaints like "I didn't make this £89 payment", "I cancelled my subscription and they still charged me" or "my sofa never arrived". These are called **disputes**. For each one, a staff member has to:

1. Work out what kind of problem it is.
2. Look up the payment and the customer's history.
3. Decide whether the customer is right.
4. If they are, claw the money back from the shop. That is a **chargeback**, and it needs a written evidence pack.

It is slow, repetitive work, which makes it a good fit for an AI agent.

## The three kinds of dispute

Telling these three apart is the core job of the agent.

| Kind | Example | What usually happens |
| --- | --- | --- |
| Real fraud | A criminal used the card at 3am in Romania | Refund the customer and charge it back |
| Merchant problem | The shop didn't deliver, or kept charging after the customer cancelled | Charge it back to the shop |
| Friendly fraud | The customer says "not me", but they have bought there before on their own phone. Often a family member made the purchase | Usually decline the claim |

## What we're building

The agent works like an assembly line. Each step does one job and passes a growing "case file" to the next.

```
load_case -> vulnerability_check -+-- HIGH risk (crisis, self-harm) --> FAST LANE: straight to a human now
                                  |
                                  +-- none / low / medium (flag carried through every step)
                                  v
             classify_dispute -> triage -+-> fraud_specialist ----------+
                                         +-> merchant_specialist -------+-> draft_evidence_pack <--+
                                         +-> friendly_fraud_specialist -+          |               | not good enough
                                                                                   v               | (max 2 retries)
                                                                            quality_review --------+
                                                                                   | passed
                                                                                   v
                                                         human_approval -> customer_letter -> finalise
```

Steps marked **NEW** are additions to the starter code.

1. **Load the case:** pull up the complaint, the customer, the payment, earlier purchases at that shop and the customer's past disputes.
2. **NEW: Vulnerability check (AI + rules):** right at the start, decide whether the customer may be vulnerable (see [Vulnerable customers](#vulnerable-customers) below). High risk goes straight to a human. Otherwise the flag travels with the case and changes how every later step treats the customer.
3. **Classify (AI):** read the complaint and pick a reason code, such as "fraud", "goods not received" or "cancelled subscription".
4. **Triage (plain rules, no AI):** choose which of the three kinds it is. A "not me" claim is treated as likely friendly fraud if at least two of these are true:
   - they have bought from this shop before
   - the payment came from their own device and passed the bank's security check (3-D Secure)
   - they have lost fraud claims before
5. **Specialist (AI):** one of three expert agents (fraud, merchant or friendly fraud) reviews the evidence and recommends an action: file a chargeback, refund the customer, or decline the claim.
6. **Evidence pack (AI):** writes the case up as a tidy report.
7. **NEW: Quality review agent (AI):** a checker reads the evidence pack before any human sees it. Is every claim backed by real data? Did it make anything up? Does the recommendation match the evidence? Was a vulnerable customer handled carefully? If not, it sends the pack back with feedback to be rewritten, at most twice.
8. **Human approval:** the agent pauses, and a member of staff approves the recommendation or overrides it. The AI never has the final say.
9. **NEW: Customer communication agent (AI):** writes the letter to the customer explaining the outcome. Gentler and clearer for vulnerable customers, never accusing anyone in friendly-fraud cases, in line with the FCA's Consumer Duty.
10. **Remember:** the outcome is saved to the customer's history, so their next dispute takes it into account.

## Vulnerable customers

Vulnerability changes **how** we handle a case, not **whether** the customer is right: the decision still rests on the evidence, but the customer gets more care, priority and a human. This follows the FCA's Consumer Duty and its guidance on vulnerable customers.

**What the check looks at:** the complaint text ("I've just lost my job", "I'm really struggling"), the customer's profile (already marked vulnerable) and their history (many disputes or repeated contact in a short time).

**What it returns:** vulnerable yes or no, the type (health, life event, low financial resilience, low capability), the level (none, low, medium or high) and the reasons.

| Level | Example | What happens |
| --- | --- | --- |
| High | "I can't cope anymore", mentions of self-harm, severe distress | **Fast lane:** skip the investigation and go straight to a human, with a short summary |
| Low or medium | Job loss, illness, money worries, confusion about the process | Normal investigation, but moved up the queue, **always** reviewed by a human, gentler letter with a support contact |
| None | "This charge isn't mine" | Normal flow. Anger ("I'm furious!") alone is not vulnerability |

**How later steps use the flag:**

- **Specialist:** consider refunding while investigating if the customer can't afford the loss. Handle friendly-fraud cases with extra care.
- **Evidence pack:** a "Vulnerability" section saying what was found and what was adjusted.
- **Quality review:** checks the vulnerability was handled appropriately.
- **Human approval:** always a human, with the vulnerability shown at the top.
- **Customer letter:** simpler words, a support contact, no pressure, longer deadlines.
- **Remember:** save the flag for next time. It is sensitive data, so store only what's needed.

## Example from a test run

The starter already works end to end. The customer wrote: *"I paid for a sofa three weeks ago and it never arrived… I'm really struggling."*

- The agent classified it as **goods not received**.
- It sent the case to the **merchant specialist**.
- It recommended **filing a chargeback**.
- It flagged the customer as **vulnerable**.
- It wrote a full evidence pack, then paused for a person to approve it.

Sample cases in `data/complaints.jsonl`: CMP-001 (true fraud), CMP-002 (cancelled subscription), CMP-003 (non-delivery, vulnerable customer), CMP-004 (friendly fraud: earlier purchases at the merchant plus two lost fraud claims).

## Why judges should like it

- **Real value:** every bank handles disputes. There is a clear user (the disputes team) and a measurable win (less handling time, fewer bad chargebacks).
- **Technical depth:** a classifier, three specialist agents, a quality reviewer that sends weak work back (agents checking each other), a customer letter writer, memory of each customer and a human approval step.
- **Measurable:** the eval script checks how often the agent picks the right reason code and the right specialist. We can show the score before and after improving a prompt.
- **Good live demo:** type "I didn't make this payment", show the steps in LangSmith Studio, show the reviewer sending a weak pack back, click approve, then show the customer letter.

## What's in this folder

It is a working starting point, not a finished product.

| File | What it is |
| --- | --- |
| `data/` | Fake customers, payments, past disputes and 4 sample complaints with the right answers attached |
| `agent.py` | Connects the steps in order |
| `utils/nodes.py` | What each step actually does |
| `utils/state.py` | The fields in the case file. Agree this as a team first, because everyone's code depends on it |
| `tools/` | Look-ups for the case data, and saving outcomes to the customer's history |
| `evals/run_evals.py` | Scores the agent in LangSmith |
| `README.md` | This plan |

## The 4 roles

Each person can work in parallel once we have agreed the case-file fields (`utils/state.py`) in the first hour. Put your name next to a role.

| Role | In short | Delivers | Owner |
| --- | --- | --- | --- |
| 1. Data and evals + quality review agent | Create the fake world the agents work in, measure how well they do, and build the checker | Dataset, baseline and improved scores, the quality reviewer | Asia |
| 2. Graph and routing | Own the assembly line, including the review loop and the letter step | Working end-to-end run by midday |  |
| 3. Specialists and evidence pack | Make the three expert agents smart and the report professional | Good recommendations and a polished evidence pack that improves on feedback |  |
| 4. Memory, approval, customer letter and demo | The human side, the agent's memory, the customer letter and the pitch | The customer letter agent, the demo and the slides |  |

### 1. Data and evals

- Grow the sample complaints from 4 to 20–30 in `data/complaints.jsonl`, each with the right answer attached (expected reason code and expected specialist).
- Cover all three kinds and every reason code, including some tricky or vague ones.
- Add matching customers, payments and past disputes so each case makes sense. For example, a friendly-fraud case needs earlier purchases at the same shop.
- Include vulnerable customers, with labels for each complaint: vulnerable yes or no, type and level. Add tricky cases: subtle signals ("I've had a hard few months"), anger that isn't vulnerability ("I'm furious!") and at least one high-risk case that must go to the fast lane.
- **NEW: score the vulnerability check.** It must catch every high-risk case (missing one is the worst mistake), and we track how many low/medium cases it spots and how many false alarms it raises.
- Run `evals/run_evals.py` to get accuracy scores in LangSmith.
- Find where the agent gets things wrong, help fix a prompt, and re-run to show the score going up. That before-and-after is a key demo moment.
- **NEW: build the quality review agent.** Write its checklist (evidence backs every claim, nothing made up, recommendation matches evidence, vulnerable customers handled carefully). It returns pass or fail with feedback.
- **NEW: test the reviewer.** Plant a few deliberately bad evidence packs and score how many it catches.

**Files:** `data/`, `evals/`, and `quality_review` in `utils/nodes.py`

### 2. Graph and routing (the "glue" person)

- Own `utils/state.py`, the case-file fields. Nobody changes it without telling this person.
- Own `agent.py`, the order the steps run in.
- Improve the classifier prompt in `classify_dispute` so it picks the right reason code more often, working with the data and evals person.
- Tune the triage rules: which signals mean friendly fraud, and how many are needed.
- **NEW: wire the review loop.** If `quality_review` fails the pack, send it back to `draft_evidence_pack` with the feedback, at most 2 times, then on to the human either way.
- **NEW: add the `customer_letter` step** after human approval, and the new fields in `utils/state.py` (review result, feedback, retry count, letter).
- **NEW: wire the vulnerability check** straight after `load_case`, with the high-risk fast lane to a human, and add its fields to `utils/state.py` (vulnerable, type, level, reasons).
- Optional extras: send cases the agent isn't sure about straight to a human, or put vulnerable customers in a priority queue.
- Move vulnerability detection out of `classify_dispute` (it currently returns `vulnerable_signals`) into the new step.
- Merge everyone's work into one branch and keep it running end to end.

**Files:** `agent.py`, `utils/state.py`, and `load_case`, `classify_dispute` and `triage` in `utils/nodes.py`

### 3. Specialist agents and evidence pack

- **Fraud specialist:** check for warning signs such as a country different from the customer's home, an unknown device, a failed security check or an odd hour.
- **Merchant specialist:** check cancellation dates against the charge date, delivery times, and duplicate amounts.
- **Friendly-fraud specialist:** be fair and careful. Often a family member made the purchase, so the customer isn't lying on purpose.
- Give each specialist its own prompt, and possibly its own tools.
- Make the evidence pack read like a real bank document, with clear sections and the evidence quoted.
- **NEW:** when the quality reviewer sends the pack back, rewrite it using the reviewer's feedback.
- **NEW:** use the vulnerability flag. Specialists consider refunding while investigating when the customer can't afford the loss, and the evidence pack gets a "Vulnerability" section.
- Optional: make each specialist a mini agent that decides which data to look up, rather than receiving everything up front.

**Files:** the three `*_specialist` functions and `draft_evidence_pack` in `utils/nodes.py`

### 4. Memory, human approval and demo

- Make the approval step clear for the reviewer: what the agent recommends, why, and whether the customer is vulnerable. The reviewer can approve or override.
- Make memory visible. On a repeat dispute the agent should say something like "this customer has lost 2 earlier fraud claims".
- Optional: move memory from the data file to LangGraph's built-in store, which shows up nicely in LangSmith.
- **NEW: build the vulnerability check** (`vulnerability_check`): read the complaint, profile and history, and return vulnerable yes or no, type, level and reasons. High risk triggers the fast lane.
- **NEW: show vulnerability at the top of the human approval screen**, and handle the fast-lane case there.
- **NEW: build the customer communication agent.** After approval, write the letter to the customer: plain English, what happens next and when, gentler for vulnerable customers, never accusing anyone in friendly-fraud cases.
- Write the 5-minute demo script and slides: the problem, how it works, the live demo, the eval scores and the impact.
- Pick the 2–3 best example complaints for the live demo, and rehearse.

**Files:** `vulnerability_check`, `human_approval`, `customer_letter` and `finalise` in `utils/nodes.py`, `tools/memory.py`, the slides

## Timeline

The goal is one complaint running all the way through by midday, then improve and measure.

| When | Everyone |
| --- | --- |
| First hour | Agree the case-file fields and data format together |
| Midday | One complaint runs end to end, even if the agents are basic. The reviewer and letter can be simple stubs at first |
| Afternoon | Each person improves their part. Run the evals, fix one thing and show the score improve |
| Last hour | Stop adding features, rehearse the demo, push the code |

**Who depends on whom:**

- The data person feeds the evals, and works with the graph person on the classifier.
- The specialists need the case-file fields from the graph person.
- The quality reviewer (role 1) and the evidence pack writer (role 3) agree what "good enough" means, so the feedback is useful.
- The graph person wires the review loop and the letter step once roles 1 and 4 have first versions.
- The vulnerability check (role 4) needs its labels and tricky cases from the data person (role 1), and its fields wired in by the graph person (role 2).
- The demo person needs everyone's work by mid-afternoon.
