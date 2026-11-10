from langgraph.graph import END, START, StateGraph

from utils.nodes import extract_profile, screen_customer, assess_risk, auto_approve, human_review, write_decision, route_on_risk
from utils.state import MyAgentContext, MyAgentInput, MyAgentState

#
# LangGraph Agent Definition
#
def build_agent(**kwargs):
    builder = StateGraph(state_schema=MyAgentState, context_schema=MyAgentContext, input_schema=MyAgentInput)

    builder.add_node("extract_profile", extract_profile)
    builder.add_node("screen_customer", screen_customer)
    builder.add_node("assess_risk", assess_risk)
    builder.add_node("auto_approve", auto_approve)
    builder.add_node("human_review", human_review)
    builder.add_node("write_decision", write_decision)

    builder.add_edge(START, "extract_profile")
    builder.add_edge("extract_profile", "screen_customer")
    builder.add_edge("screen_customer", "assess_risk")
    builder.add_conditional_edges("assess_risk", route_on_risk)
    builder.add_edge("auto_approve", "write_decision")
    builder.add_edge("human_review", "write_decision")
    builder.add_edge("write_decision", END)

    return builder.compile(**kwargs)

agent = build_agent()

# Example invoke:
# agent.invoke({"customer_id": "CUST-001"})
