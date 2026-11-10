"""Module 2: evaluate the agent's scoring against analysts' labels with LangSmith.

One dataset example is one customer's whole transaction stream, because the agent's decisions depend on memory.
The target function runs a fresh agent over the stream, and three plain-Python evaluators compare its decisions
with the analysts' labels in `data/analyst_labels.jsonl`.
"""
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langsmith import Client, evaluate

from agent import build_agent
from evals.scoring_evals import decision_accuracy_eval, score_alignment_eval, review_routing_eval
from utils import db
from utils.attendee import ATTENDEE
from utils.state import OCT_19, MyAgentContext
from utils.transaction_processing import transaction_stream, process_transactions, decision_log

DATASET_NAME = f"transaction-monitoring-{ATTENDEE}"

REVIEWER_ANSWERS = {row["transaction_id"]: row for row in db.table("reviewer_answers")}
EVALUATORS = [decision_accuracy_eval, score_alignment_eval, review_routing_eval]

def _build_dataset_examples() -> list[dict]:
    """One example per customer: their stream and reviewer answers as inputs, the analysts' labels as outputs."""
    examples = []
    for customer in db.table("customer_profiles"):
        transactions = transaction_stream(customer["user_id"], end=OCT_19)
        ids = [tx["transaction_id"] for tx in transactions]
        examples.append(
            {
                "inputs": {
                    "user_id": customer["user_id"],
                    "transactions": transactions,
                    "reviewer_answers": {tx_id: REVIEWER_ANSWERS[tx_id] for tx_id in ids if tx_id in REVIEWER_ANSWERS},
                },
                "outputs": {"labels": {tx_id: db.get("analyst_labels", transaction_id=tx_id) for tx_id in ids}},
            }
        )
    return examples

def _create_dataset(client: Client) -> None:
    """Create the LangSmith dataset if this attendee doesn't have one yet."""
    if not client.has_dataset(dataset_name=DATASET_NAME):
        dataset = client.create_dataset(DATASET_NAME, description="Customer transaction streams with analyst labels")
        client.create_examples(dataset_id=dataset.id, examples=_build_dataset_examples())

def run_agent_on_stream(context_settings: MyAgentContext, inputs: dict) -> dict:
    """The target function: run a fresh agent over one customer's stream and return its decisions."""
    agent = build_agent(checkpointer=InMemorySaver(), store=InMemoryStore() )

    process_transactions(
        agent, context_settings=context_settings, transactions=inputs["transactions"], thread_id=inputs["user_id"], reviewer_answers=inputs["reviewer_answers"]
    )

    return {"decisions": decision_log(agent, inputs["user_id"])}

def average_scores(results) -> dict:
    """Average each evaluator's score across all the examples in an experiment."""
    scores = {}
    for row in results:
        for result in row["evaluation_results"]["results"]:
            scores.setdefault(result.key, []).append(result.score)

    return {key: round(sum(values) / len(values), 2) for key, values in scores.items()}

def run_experiment(prefix: str, context_settings: MyAgentContext) -> dict:
    """Run the evaluation once and return the average scores. The full experiment is saved to LangSmith."""
    
    # `evaluate` calls the target with just the example's inputs, so bind the settings here.
    def target(inputs: dict) -> dict:
        return run_agent_on_stream(context_settings, inputs)

    results = evaluate(target, data=DATASET_NAME, evaluators=EVALUATORS, experiment_prefix=prefix, max_concurrency=2)
    
    return average_scores(results)

#
# MAIN: Run the experiment, for the baseline and then again with an updated confidence threshold
#
if __name__ == "__main__":
    _create_dataset(Client())

    print("baseline:", run_experiment(prefix="baseline", context_settings=MyAgentContext()))
    print("threshold-0.8:", run_experiment(prefix="threshold-0.8", context_settings=MyAgentContext(confidence_threshold=0.8)))
