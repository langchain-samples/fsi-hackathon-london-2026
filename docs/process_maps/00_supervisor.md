# 00 Supervisor (Control Tower): process map

**Owner:** Lukman · **Code:** `00_control_tower/`

## What it does

Takes a request ("PS26-7 has been published", "onboard CUST-026", "monitor this customer") and runs the right
agent, in the right order. It never makes a KYC or fraud decision itself, and it never approves on a human's behalf.

## Flow

1. **Regulation arrives** → asks the 03 agent for a brief → turns the brief into control changes (policy) → parks
   them for a human to approve.
2. **Policy approved** → stored as the live policy for the firm.
3. **Customer onboarded or re-screened** → runs 01 → adds the live policy's rules → stores the customer's rating.
4. **Transactions arrive** → runs 02 with thresholds set by that rating → stores decisions and alerts.
5. **Too many alerts** → flags the customer → goes back to step 3.

## Shared memory it manages

| Item | Written by | Read by |
|------|-----------|---------|
| Live policy (per firm) | 03, after human approval | 01, 02 |
| Customer risk rating and reasons | 01 | 02, supervisor |
| Alerts and KYC-refresh flags | 02 | supervisor → 01 |
| Review cases (anything waiting for a human) | all | portal |

## What it shows

The portal (`00_control_tower/portal`): story buttons, customer cards, review inbox, live policy, activity feed.
One LangSmith trace per run, with every agent nested inside it.
