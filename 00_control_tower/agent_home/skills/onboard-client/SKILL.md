---
name: onboard-client
description: Use when a new client is to be onboarded, or when the user asks to onboard, screen, rate or re-assess a named client (CUST-xxx) under the live policy. Lifecycle stage onboarding -> active or under_review.
---

# Onboard a client

The KYC pillar (project 01) screens the client and rates them under the live policy. You run it and report; you do
not rate anyone yourself.

1. Call `customer_view` for the client. If they already have a rating under the live policy and the user did not
   ask for a re-assessment, say so and stop: do not screen twice.
2. Call `onboard_customer` with the customer ID.
3. Report, in this order: the rating, each reason with its paragraph number, and the status.
4. If the status is `pending_review`, the client is now `under_review`. Call `open_reviews`, name the case ID and
   tell the user it is waiting in the review inbox. Stop there.

Never approve or reject a client. Only a human does that, in the review inbox.
