from dataclasses import replace

from langgraph.types import Command

from utils import db


def transaction_stream(user_id: str, start: str | None = None, end: str | None = None) -> list[dict]:
    """A customer's transactions from the database, oldest first, from `start` up to (not including) `end`."""
    transactions = [
        tx
        for tx in db.query("transactions", user_id=user_id)
        if (start is None or tx["timestamp"] >= start) and (end is None or tx["timestamp"] < end)
    ]
    return sorted(transactions, key=lambda tx: tx["timestamp"])

def transaction_message(transaction_id: str) -> dict:
    """Turn a transaction ID into an input message for the agent. The agent looks up everything else itself."""
    return {"messages": [{"role": "user", "content": f"New transaction: {transaction_id}"}]}

def process_transactions(agent, context_settings, transactions: list[dict], thread_id: str, reviewer_answers: dict | None = None):
    """Send transactions to the agent one at a time, all on the same conversation thread.

    Takes in: a compiled agent `app`, the transactions to send, the `thread_id` to send them on, optionally a
    dict of what a reviewer answers for each transaction ID.
    Does: invokes the agent once per transaction. If the agent pauses for a human, answers from `reviewer_answers`,
    or accepts the agent's own decision if there is no answer for that transaction.
    Returns: nothing; prints each decision as it goes.
    """
    config = {"configurable": {"thread_id": thread_id}}
    for tx in transactions:
        context = replace(context_settings, user_id=tx["user_id"])
        result = agent.invoke(transaction_message(tx["transaction_id"]), config, context=context)
        while "__interrupt__" in result:
            question = result["__interrupt__"][0].value
            answer = (reviewer_answers or {}).get(question["transaction_id"], {"decision": question["agent_decision"]})
            print(f"  [human review requested for {question['transaction_id']}: {answer['decision']}]")
            result = agent.invoke(Command(resume=answer), config, context=context)
        print(f"{tx['transaction_id']}: {result['messages'][-1].text}")


def decision_log(agent, user_id: str) -> list[dict]:
    """Every decision recorded for a customer, in transaction order, read back from long-term memory."""
    items = agent.store.search(("decisions", user_id), limit=1000)
    
    return sorted((item.value for item in items), key=lambda decision: decision["transaction_id"])
