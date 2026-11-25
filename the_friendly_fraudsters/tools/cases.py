"""Look up the facts behind a dispute: the complaint, the customer, the transaction and related history."""

from utils import db


def get_complaint(complaint_id: str) -> dict:
    row = db.get("complaints", complaint_id=complaint_id)
    if row is None:
        raise ValueError(f"There is no complaint {complaint_id}.")
    return row


def get_customer(customer_id: str) -> dict:
    return db.get("customers", customer_id=customer_id) or {}


def get_transaction(transaction_id: str) -> dict:
    return db.get("transactions", transaction_id=transaction_id) or {}


def get_merchant_history(customer_id: str, merchant: str, exclude_transaction_id: str) -> list[dict]:
    """Earlier transactions by the same customer at the same merchant (signal for friendly fraud)."""
    return [
        tx for tx in db.query("transactions", customer_id=customer_id, merchant=merchant)
        if tx["transaction_id"] != exclude_transaction_id
    ]


def get_dispute_history(customer_id: str) -> list[dict]:
    """The customer's past disputes. This is the agent's long-term memory of the customer."""
    return db.query("dispute_history", customer_id=customer_id)
