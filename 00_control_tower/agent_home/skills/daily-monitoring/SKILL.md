---
name: daily-monitoring
description: Use when the user asks to run transaction monitoring for a client, asks about a client's card transactions or alerts, or asks for today's monitoring. Lifecycle stage active; may lead to under_review or refresh_due.
---

# Daily transaction monitoring

The TM pillar (project 02) decides each card payment in layers. A rules engine in code scores it and puts it in a band
(low, medium or high); the cut-offs come from the client's KYC rating and the live policy, so a client must be
onboarded first. Low payments are approved by the rules. Medium and high ones go to an investigator model, which
explains them and proposes a next step, then an auditor model, which checks the explanation and every fact it cites.
A person decides every medium or high payment. No model approves or blocks anything.

1. Call `customer_view`. If the client has no KYC rating, say they must be onboarded first and stop.
2. Call `monitor_customer` with the customer ID.
3. Report each decision as: transaction ID, band and rules fired (with their paragraphs), who decided (rules or
   human), and the decision. For a payment a person decided, add the investigator's proposed next step and the audit
   verdict. Then the alerts.
4. Explain a decision from its band and the rules that fired, not from a model's confidence. The band is what sent it
   to a person.
5. If `waiting_for_review` is not empty, the run has paused on a payment that needs a person. Name the case and stop.
   The run continues by itself when the reviewer answers in the inbox. Never answer it yourself.
6. Call `customer_view` again. If the stage is `refresh_due`, say why and by when (the history line cites 6.5 and the
   due date under 5.5) and offer the `kyc-refresh` skill. Do not run it unasked.

State the rule set and cut-offs that were used (they are in the client history) so the user can see the rating at work.
