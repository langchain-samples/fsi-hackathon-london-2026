"""Record the agent's decision on a transaction, and ask a human when the agent shouldn't decide alone."""

from typing import Literal

from langchain.tools import ToolRuntime, tool
from langgraph.types import interrupt

from utils import db
from utils.state import MyAgentContext


@tool(parse_docstring=True)
def record_decision(
    transaction_id: str,
    risk_score: int,
    confidence: float,
    decision: Literal["approve", "block"],
    reason: str,
    runtime: ToolRuntime[MyAgentContext],
) -> str:
    """Record your decision on a transaction. Call this exactly once for every transaction.

    Args:
        transaction_id: The ID of the transaction you assessed.
        risk_score: How likely the transaction is to be fraud, from 0 (certainly fine) to 100 (certainly fraud).
        confidence: How sure you are of your score, from 0 (guessing) to 1 (certain).
        decision: "approve" to let the payment through, or "block" to stop it.
        reason: One or two sentences explaining your score.
    """
    ctx = runtime.context
    user_id = ctx.user_id
    model_confidence = confidence

    transaction = db.get("transactions", transaction_id=transaction_id, user_id=user_id)
    checks = _confidence_checks(risk_score, transaction, user_id, ctx.grey_zone)
    if checks:
        confidence = min(confidence, ctx.checked_confidence)

    final_decision, decided_by = decision, "agent"
    if confidence < ctx.confidence_threshold:
        answer = interrupt(
            {
                "transaction_id": transaction_id,
                "risk_score": risk_score,
                "confidence": confidence,
                "why_review": checks or [f"the model's confidence ({model_confidence}) is low"],
                "agent_decision": decision,
                "reason": reason,
                "question": "Approve or block? Reply with {'decision': 'approve' or 'block', 'note': 'optional'}",
            }
        )
        final_decision, decided_by = answer["decision"], "human"
        if answer.get("note"):
            runtime.store.put(("customer_notes", user_id), transaction_id, {"note": answer["note"]})

    # Keep an audit record of every decision in the store as well.
    runtime.store.put(
        ("decisions", user_id),
        transaction_id,
        {
            "transaction_id": transaction_id,
            "risk_score": risk_score,
            "model_confidence": model_confidence,
            "confidence": confidence,
            "confidence_checks": checks,
            "agent_decision": decision,
            "final_decision": final_decision,
            "decided_by": decided_by,
            "reason": reason,
        },
    )
    return f"Recorded {transaction_id}: {final_decision} (decided by {decided_by})."


def _confidence_checks(
    risk_score: int, transaction: dict | None, user_id: str, grey_zone: tuple[int, int]
) -> list[str]:
    """Return the reasons a human should review this decision, whatever the model's confidence.

    Args:
        risk_score: The model's fraud risk score, from 0 to 100.
        transaction: The transaction being decided, or None if it wasn't found for this customer.
        user_id: The ID of the customer the transaction belongs to.
        grey_zone: Risk scores from grey_zone[0] up to (not including) grey_zone[1] are "unusual, but could be legitimate".

    Returns:
        A list of plain-English reasons; empty if no check fired.
    """
    reasons = []
    if grey_zone[0] <= risk_score < grey_zone[1]:
        reasons.append(f"risk score {risk_score} is in the grey zone ({grey_zone[0]}-{grey_zone[1] - 1})")

    profile = db.get("customer_profiles", user_id=user_id)
    if (
        transaction
        and risk_score < grey_zone[1]
        and transaction["country"] != profile["home_country"]
        and transaction["amount"] > profile["usual_amount_range"][1]
    ):
        reasons.append("manually approve a payment abroad that's larger than the customer usually spends")
    return reasons
