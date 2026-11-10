"""Look up a customer's account application in the bank's database (`data/applications.jsonl`)."""

from utils import db


def get_application(customer_id: str) -> str:
    """The application text a customer submitted."""
    row = db.get("applications", customer_id=customer_id)
    if row is None:
        raise ValueError(f"There is no application for customer {customer_id}.")
    return row["application"]
