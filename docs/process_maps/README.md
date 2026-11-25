# Process maps

See also the [game plan](../GAME_PLAN.md).

One page per agent: what goes in, what comes out, which tools it uses, and what it shares. The supervisor agent
(00) is built against these pages, so it can combine the three agents without knowing how each one works inside.

High level is enough. Describe the *shape* of the data ("a customer ID in, a risk rating with reasons out"), not
real values. Use Alejandro's map for 03 as the example.

| Agent | Owner | Page | Status |
|-------|-------|------|--------|
| 00 Supervisor (Control Tower) | Lukman | [00_supervisor.md](00_supervisor.md) | Drafted |
| 01 KYC onboarding | Nam | [01_kyc.md](01_kyc.md) | Pre-filled from the starter code: Nam to check and amend |
| 02 Transaction monitoring | Marcus | [02_transaction_monitoring.md](02_transaction_monitoring.md) | Pre-filled from the starter code: Marcus to check and amend |
| 03 Regulatory change | Alejandro | [03_regulatory_change.md](03_regulatory_change.md) | From Alejandro's process map (Version B) |

Each page has a **"For the supervisor"** box at the top. That box is the contract: if you change what your agent
takes in or gives back, update the box in the same pull request.
