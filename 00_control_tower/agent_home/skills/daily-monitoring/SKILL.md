---
name: daily-monitoring
description: Use when the user asks to run transaction monitoring for a client, asks about a client's card transactions or alerts, or asks for today's monitoring. Lifecycle stage active; may lead to under_review or refresh_due.
---

# Daily transaction monitoring

The TM pillar (project 02) scores each card transaction. Its thresholds come from the client's KYC rating and the
live policy, so a client must be onboarded first.

1. Call `customer_view`. If the client has no KYC rating, say they must be onboarded first and stop.
2. Call `monitor_customer` with the customer ID.
3. Report each decision as: transaction ID, decision, who decided (agent or human). Then the alerts.
4. If `waiting_for_review` is not empty, the run has paused on a payment that needs a human. Name the case and
   stop. The run continues by itself when the reviewer answers in the inbox.
5. Call `customer_view` again. If the stage is `refresh_due`, say why (the history line cites the paragraph) and
   offer the `kyc-refresh` skill. Do not run it unasked.

State the thresholds that were used (they are in the client history) so the user can see the rating at work.
