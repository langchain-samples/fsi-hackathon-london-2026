from typing_extensions import TypedDict

class MyAgentContext(TypedDict):
    """The agent configuration context that is not exposed to the user directly.
    
    LangSmith studio will show this under the "assistant" option and it can be used by the assistant API to configure parameters for an instance of the agent.
    """
    agent_model: str

class MyAgentInput(TypedDict):
    """The agent public input schema.

    Callers (and LangSmith Studio) only supply a customer_id; every other state key is filled in by the
    nodes, so exposing just this keeps the input form minimal.
    """
    customer_id: str            # whose application to check, for example "CUST-001"

class MyAgentState(MyAgentInput):
    """The shared state within the agent.

    Every node reads from it and returns only the keys it updates, which LangGraph merges back in, so it
    acts as the case file that carries data between steps of the graph.

    Extends the input to contain additional fields.
    """
    application: str            # the raw application text, looked up by extract_profile
    profile: dict               # set by extract_profile
    screening_hits: list[dict]  # set by screen_customer
    risk_level: str             # "low" or "high", set by assess_risk
    risk_reasons: list[str]     # why we chose that risk level
    decision: str               # "approve" or "reject"
    reviewer: str               # "system" or "human"
    reviewer_notes: str
    decision_memo: str          # set by write_decision

