---
name: kyc-refresh
description: Use when a client is flagged for a KYC refresh (stage refresh_due), or the user asks to process KYC refresh flags raised by transaction monitoring.
---

# KYC refresh

Transaction monitoring flags a client for a refresh when their alerts reach the policy's limit inside the window.

1. Call `client_register` and list the clients whose stage is `refresh_due`, each with the reason from their
   history and its paragraph number.
2. If the user asked to process them, call `process_kyc_refresh`. It re-rates every flagged client.
3. Report each client's rating before and after, with the reasons and paragraphs.
4. If a client comes back `pending_review`, name the case from `open_reviews` and stop.

If nobody is flagged, say so in one line.
