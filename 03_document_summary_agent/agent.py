"""Document summary agent: a LangChain deep agent that briefs a regulation, then a reviewer summary on top.

Given a document ID (and optionally a firm ID), it runs two steps:

1. `brief` (a deep agent):
   - loads the document with `get_document`, which returns it already split into sections;
   - hands each section to a `section-summariser` sub-agent, which extracts its obligations as a `SectionSummary`;
   - gets the firm's facts with `get_firm_facts` (profile, senior managers, important business services, the
     small-firm test and data gaps, all worked out in code, so the model never compares numbers itself);
   - combines them into a `Brief`: one item per obligation, with what it means for the firm (`brief.py`).
2. `review`: writes the reviewer summary, sections A to E in MillTech's horizon-scanning format, on top of the Brief,
   with the deadlines and counts worked out in code (`review.py`).

The result is a `ReviewedBrief` in `result["structured_response"]`: the Brief, plus `review`, `key_dates` and
`counts`. `instructions.md` and `reviewer_instructions.md` are the two prompts.
"""
from typing import Any

from brief import Brief
from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from langgraph.graph import END, START, MessagesState, StateGraph
from review import review_brief
from sub_agent import section_summariser_subagent
from tools.documents import get_document
from tools.firm_facts import get_firm_facts
from utils.models import PROJECT_ROOT, model

INSTRUCTIONS = (PROJECT_ROOT / "instructions.md").read_text()


class BriefState(MessagesState):
    """The conversation plus the structured result: a Brief after step 1, a ReviewedBrief after step 2."""

    structured_response: Any


def build_briefer():
    """Step 1 on its own: the deep agent that returns a Brief."""
    return create_deep_agent(
        model=init_chat_model(model),
        tools=[get_document, get_firm_facts],
        system_prompt=INSTRUCTIONS,
        subagents=[section_summariser_subagent],
        response_format=Brief,
        name="briefer",
    )


def build_agent(**kwargs):
    """Build the runnable agent (brief, then review). Pass `checkpointer` and `store` to give it memory."""
    reviewer = init_chat_model(model)

    def review(state: BriefState) -> dict:
        brief = state["structured_response"]
        brief = brief if isinstance(brief, Brief) else Brief.model_validate(brief)
        return {"structured_response": review_brief(brief, reviewer)}

    graph = StateGraph(BriefState)
    graph.add_node("brief", build_briefer())
    graph.add_node("review", review)
    graph.add_edge(START, "brief")
    graph.add_edge("brief", "review")
    graph.add_edge("review", END)
    return graph.compile(name="document-summary-agent", **kwargs)


agent = build_agent()

# Example invoke:
# result = agent.invoke({"messages": [{"role": "user", "content": "Summarise document PS26-4 for FIRM-001"}]})
# result["structured_response"].review   # the reviewer summary, sections A to E
