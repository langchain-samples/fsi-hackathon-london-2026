def _decisions_by_id(outputs: dict) -> dict:
    return {decision["transaction_id"]: decision for decision in outputs["decisions"]}

def decision_accuracy_eval(outputs: dict, reference_outputs: dict) -> float:
    """Share of transactions where the agent's own decision matches the analysts'."""
    decisions = _decisions_by_id(outputs)
    labels = reference_outputs["labels"]
    matches = [
        tx_id in decisions and decisions[tx_id]["agent_decision"] == label["decision"]
        for tx_id, label in labels.items()
    ]
    return sum(matches) / len(matches)


def score_alignment_eval(outputs: dict, reference_outputs: dict) -> float:
    """1 minus the average gap between the agent's and the analysts' risk scores, on a 0-1 scale."""
    decisions = _decisions_by_id(outputs)
    labels = reference_outputs["labels"]
    gaps = [
        abs(decisions[tx_id]["risk_score"] - label["risk_score"]) if tx_id in decisions else 100
        for tx_id, label in labels.items()
    ]
    
    return 1 - (sum(gaps) / len(gaps) / 100)

def review_routing_eval(outputs: dict, reference_outputs: dict) -> float:
    """Share of transactions where the agent asked a human exactly when analysts said it should."""
    decisions = _decisions_by_id(outputs)
    labels = reference_outputs["labels"]
    correct = [
        tx_id in decisions and (decisions[tx_id]["decided_by"] == "human") == label["ambiguous"]
        for tx_id, label in labels.items()
    ]
    return sum(correct) / len(correct)
