"""Transaction monitoring: rules triage in code, an investigator and an auditor model, and a person for every alert.

One LangGraph graph per customer run. Send it one message per transaction ("New transaction: TX-2004") on the same
thread, with a `MyAgentContext` for the customer. It pauses with `interrupt()` whenever a person must decide, and
writes every decision to the store under ("decisions", user_id). See README.md for the nine steps.
"""

from typing import Annotated, TypedDict

from langchain.chat_models import init_chat_model
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from utils.models import PROJECT_ROOT, model  # noqa: F401  (PROJECT_ROOT kept for notebooks that import it here)
from utils.nodes import (after_audit, after_investigate, after_load, after_triage, load_context, make_audit,
                         make_investigate, record_decision, triage)
from utils.state import MyAgentContext, TmState


class TmInput(TypedDict):
    """What a run takes: a message naming the transaction, e.g. "New transaction: TX-2004"."""
    messages: Annotated[list, add_messages]


def build_agent(**kwargs):
    """Build the transaction-monitoring graph. Pass `checkpointer` and `store` to give it memory."""
    llm = init_chat_model(model)  # the auditor uses the same model as the investigator: a weaker checker misses more
    graph = StateGraph(TmState, context_schema=MyAgentContext, input_schema=TmInput)
    graph.add_node("load_context", load_context)
    graph.add_node("triage", triage)
    graph.add_node("investigate", make_investigate(llm))
    graph.add_node("audit", make_audit(llm))
    graph.add_node("record_decision", record_decision)
    graph.add_edge(START, "load_context")
    graph.add_conditional_edges("load_context", after_load, ["triage", END])
    graph.add_conditional_edges("triage", after_triage, ["investigate", "record_decision"])
    graph.add_conditional_edges("investigate", after_investigate, ["audit", "record_decision"])
    graph.add_conditional_edges("audit", after_audit, ["investigate", "record_decision"])
    graph.add_edge("record_decision", END)
    return graph.compile(name="transaction_monitoring_agent", **kwargs)


agent = build_agent()

# Example invoke:
# agent.invoke({"messages": [{"role": "user", "content": "New transaction: TX-2004"}]},
#              {"configurable": {"thread_id": "tm-USR-002"}}, context=MyAgentContext(user_id="USR-002"))
