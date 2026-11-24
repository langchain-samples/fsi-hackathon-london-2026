# 03 Regulatory change (compliance brief): process map

**Owner:** Alejandro · **Code:** `03_document_summary_agent/` · **Built with:** deep agent with parallel sub-agents

> From Alejandro's *Project 03 process map: compliance brief agent* (24 Nov 2026), Version B, which aligns the
> starter with the Horizon Scanner. Alejandro: add the PDF next to this page as `03_regulatory_change.pdf`.

## For the supervisor (the contract)

| | Shape |
|---|---|
| **In** | A document ID and one or more firm IDs, e.g. `PS26-7` for `FIRM-001` |
| **Out** | A brief per firm: section summaries, obligations with paragraph, deadline and verbatim quote, and whether each applies to the firm |
| **Reads from shared memory** | Reviewer corrections from earlier runs |
| **Writes to shared memory** | Through the supervisor: the control changes the brief implies (after a human approves them) |
| **Asks a human when** | Applicability is unclear; and to approve or correct each brief |

## Rules the build must respect (from Alejandro's map)

- Scope decides everything: check scope first; an out-of-scope firm gets a one-line "does not apply" brief.
- Definitions travel across sections: pass the scope and definitions sections to every sub-agent.
- Later sections override earlier deadlines (transitional arrangements): compute each firm's effective deadline after all sections are summarised.
- Firm data creates firm-specific dates: compute them in code, not with the model.
- Missing firm data is a gap, and a gap is an action in the brief.
- A missed obligation costs more than an extra one: optimise for recall; send "unclear" to the reviewer.
- Every statement must be traceable to a paragraph and a verbatim quote.

## Version B stages, in build order

1. Starter running end to end (fallback and 11:30 checkpoint).
2. Gold set and evals: quote fidelity, deadline accuracy (code); recall, precision (LLM judge); applicability (code).
3. Sections A–E brief format (presentation agent).
4. Extra firms and per-firm applicability: FIRM-002 (small e-money institution), FIRM-003 (investment firm, out of scope).
5. Re-run on failed code checks.
6. Human review (approve or correct each brief).
7. Triage tags and memory of reviewer corrections, if time allows.

## Documents

- `PS26-4`: operational resilience and third-party technology (from LangChain).
- `PS26-7`: financial crime controls (synthetic, added by us). This is the one that changes KYC and transaction monitoring.
