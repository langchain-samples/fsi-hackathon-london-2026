"""Write a finished case back to the customer's dispute history, so the next case can see it.

Owner: memory / HITL person. Swap this for a LangGraph `store` if you want memory that lives in LangSmith.
"""

import json
from datetime import date

from utils.db import DATA_DIR


def record_dispute(customer_id: str, transaction_id: str, merchant: str, reason_code: str, outcome: str, notes: str) -> dict:
    rows = (DATA_DIR / "dispute_history.jsonl").read_text(encoding="utf-8").splitlines()
    row = {
        "dispute_id": f"DSP-{1000 + len([r for r in rows if r.strip()])}",
        "customer_id": customer_id,
        "transaction_id": transaction_id,
        "merchant": merchant,
        "reason_code": reason_code,
        "outcome": outcome,
        "notes": notes,
        "opened": date.today().isoformat(),
    }
    with open(DATA_DIR / "dispute_history.jsonl", "a", encoding="utf-8") as file:
        file.write(json.dumps(row) + "\n")
    return row
