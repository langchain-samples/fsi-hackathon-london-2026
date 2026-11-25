---
name: client-briefing
description: Use when the user asks where a client is in their lifecycle, what happened to them, why their rating or thresholds changed, what is due next, or asks for an overview of all clients. Read only.
---

# Client briefing

Answer from the client file. It is written by the system, not by you, so quote it; do not add to it or guess.

For one client, call `customer_view` and reply in this shape:

- **Now**: stage, KYC rating, and the policy the rating was made under.
- **How they got here**: the history in order, one line each, with paragraph numbers where the file has them.
- **Waiting on a human**: open review cases, by case ID. Say they are answered in the review inbox.
- **Next**: what the stage calls for. `reassessment_due`: re-assess under the live policy. `refresh_due`: KYC
  refresh. `under_review`: nothing until the reviewer decides. `active`: routine monitoring.

For all clients, call `client_register` and give one line per client: name, stage, rating, policy.

This skill only reads. If the user wants something run, use the skill for that stage.
