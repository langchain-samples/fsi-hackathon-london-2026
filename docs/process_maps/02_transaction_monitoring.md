# 02 Transaction monitoring: process map

**Owner:** Marcus · **Code:** `02_transaction_monitoring_agent/` · **Built with:** LangChain agent with memory and human review

> Pre-filled from the starter code. Marcus: check each part, change what you'd do differently as a risk manager,
> and fill in the "Your ideas" section.

## For the supervisor (the contract)

| | Shape |
|---|---|
| **In** | A customer's card account (e.g. `USR-002`) and their transaction IDs, one at a time |
| **Out** | Per transaction: risk score (0–100), confidence (0–1), decision (`approve` / `block`), who decided (agent / human), reason |
| **Reads from shared memory** | The customer's KYC rating; reviewer notes from earlier cases; the live policy's thresholds |
| **Writes to shared memory** | Every decision; alerts (blocks and human referrals); a KYC-refresh flag when alerts pile up |
| **Asks a human when** | Confidence is below the threshold, the score is in the grey zone, or a large payment is made abroad |


**Contract check:** `uv run pytest -q 00_control_tower/tests/test_contracts.py`. Keep `confidence_threshold` and `grey_zone` on `MyAgentContext`, and keep writing decisions with `transaction_id`, `risk_score`, `confidence`, `final_decision`, `decided_by` and `reason`. Read the live policy in a tool with `control_tower.contracts.read_policy(runtime.store)`.

## Steps today

| Step | What happens | Tool or data |
|------|--------------|--------------|
| 1. Look up the transaction | Merchant, category, amount, country, time | `get_transaction` → `transactions.jsonl` |
| 2. Look up the customer | Usual spend range, home country, usual categories, reviewer notes | `get_customer_profile` → `customer_profiles.jsonl` + memory |
| 3. Compare | Against the profile and earlier transactions in the conversation | short-term memory |
| 4. Score and decide | Risk score, confidence, approve / block | `record_decision` |
| 5. Check | Code lowers confidence for grey-zone scores (30–69) or large payments abroad; below 0.7 → a human decides | human review |
| 6. Learn | A reviewer's note (e.g. "on holiday in Portugal") is remembered for next time | long-term memory |

## Evals already in the starter

Module 2: scores compared with analysts' labels in LangSmith. Module 3: annotation queue, where reviews improve the eval over time.

## Your ideas (Marcus to fill in)

- Thresholds for a high-risk customer (PS26-7 draft says: human if score ≥ 20 or confidence < 0.85):
- Merchant categories you'd treat as high risk:
- How many alerts, in how many days, should re-open a customer's KYC file:
