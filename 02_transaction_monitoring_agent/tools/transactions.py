"""Look up a transaction in the bank's database (`data/transactions.jsonl`)."""

from langchain.tools import ToolRuntime, tool

from utils import db
from utils.state import MyAgentContext

@tool(parse_docstring=True)
def get_transaction(transaction_id: str, runtime: ToolRuntime[MyAgentContext]) -> dict | str:
    """Look up a transaction's details: merchant, amount, country, time and so on.

    Args:
        transaction_id: The ID of the transaction, for example "TX-1003".
    """
    # Only this customer's transactions: the customer comes from the run's context, not from the model.
    transaction = db.get("transactions", transaction_id=transaction_id, user_id=runtime.context.user_id)
    return transaction or f"There is no transaction {transaction_id} for this customer."

