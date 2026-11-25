---
name: apply-regulatory-change
description: Use when a new regulation or policy statement arrives (for example PS26-7), when the user asks what a regulation changes, or asks which clients a policy change affects and to re-assess them. Covers briefing, the policy proposal, and the reassessment_due stage.
---

# Apply a regulatory change

A regulatory change moves through three gates. Find out which gate it is at before doing anything.

**Gate 1: brief and propose.** If the document has not been briefed yet, call `brief_regulation` with the
document ID. The regulatory pillar (project 03) writes the brief with a reviewer summary (sections A to E) and parks
it for a person. The result's `journey` says which kind of decision it needs:

- `control_change`: the brief changes KYC or monitoring controls. Report each control change with its paragraph
  number, give the case ID, and stop. The policy is not live.
- `sign_off`: the brief changes no control (for example PS26-4, operational resilience). Summarise the reviewer
  summary's impact and immediate actions with their deadlines, give the case ID, and stop. Gate 3 does not apply:
  no client needs re-assessing.

**Gate 2: human decision.** A reviewer approves or rejects the control changes, or signs off or sends back the
brief, in the review inbox. You cannot do this. If `open_reviews` still shows the case, say it is waiting and stop.

**Gate 3: who is affected.** Once the policy is live, call `affected_clients`. Every client last assessed under
the old policy is `reassessment_due`. For each one, say which changed controls could apply to them and cite the
paragraphs. Then ask the user which clients to re-assess.

Re-assess only the clients the user names, one `onboard_customer` call each, and report the old and new rating.
Never re-assess every client just because the list exists. Transaction monitoring needs no separate step: its
thresholds follow the new rating on the next monitoring run.
