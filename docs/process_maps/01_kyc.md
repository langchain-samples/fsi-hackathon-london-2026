# 01 KYC onboarding: process map

**Owner:** Nam · **Code:** `01_kyc_agent/` · **Built with:** LangGraph (a fixed graph of steps)

> Pre-filled from the starter code. Nam: check each part, change what you'd do differently as a KYC analyst, and
> fill in the "Your ideas" section.

## For the supervisor (the contract)

| | Shape |
|---|---|
| **In** | A customer ID, e.g. `CUST-026` |
| **Out** | Risk rating (`low` / `medium` / `high`), the reasons for it, and a decision (`approve` / `reject`, or waiting for a human) |
| **Reads from shared memory** | The live policy (e.g. whether close associates of a PEP need EDD) |
| **Writes to shared memory** | The customer's rating and reasons |
| **Asks a human when** | The customer is high risk |

## Steps today

| Step | What happens | Tool or data |
|------|--------------|--------------|
| 1. Extract profile | An LLM reads the free-text application and pulls out name, date of birth, nationality, occupation, source of funds | `get_application` → `applications.jsonl` |
| 2. Screen | Name checked against the sanctions list (name + date of birth) and the PEP list (name) | `check_sanctions`, `check_pep` |
| 3. Assess risk | Any match = high risk; no match = low risk | none (rule in code) |
| 4. Decide | Low risk: approved automatically. High risk: paused for a compliance officer | human review |
| 5. Memo | An LLM writes a short decision memo for the audit file | none |

## Gaps a KYC analyst would spot

- Only two ratings (low / high): no medium.
- Name screening only: a customer who *says* their business partner is a PEP is missed.
- Country risk, source of funds and deposit size are not used.

The Control Tower adds these from the live policy for now (`00_control_tower/control_tower/pillars/kyc.py`). Nam,
you can move them into your graph instead.

## Your ideas (Nam to fill in)

- Rating rules you would use:
- What should always go to a human:
- Gold labels: the rating you would give each of the 27 applications (this becomes our KYC eval):
