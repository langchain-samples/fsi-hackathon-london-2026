"""Look up a customer in the bank's database (`data/customer_profiles.jsonl`), plus the agent's notes on them."""

from langchain.tools import ToolRuntime, tool

from utils import db
from utils.state import MyAgentContext

@tool(parse_docstring=True)
def get_customer_profile(runtime: ToolRuntime[MyAgentContext]) -> dict:
    """Look up the customer's profile, plus any notes that human reviewers have left about them.

    The customer is the one this run is about, so no arguments are needed.
    """
    user_id = runtime.context.user_id
    profile = db.get("customer_profiles", user_id=user_id)
    notes = runtime.store.search(("customer_notes", user_id), limit=100)
    return {**profile, "reviewer_notes": [item.value["note"] for item in notes]}
