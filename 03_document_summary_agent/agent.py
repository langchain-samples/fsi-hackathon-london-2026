"""Document summary agent, built as a LangChain deep agent.

Given a document ID (and optionally a firm ID), it:
1. Loads the document with `get_document`, which returns it already split into sections.
2. Hands each section to a sub-agent, which summarises it and extracts its obligations as a `SectionSummary`.
3. Gets the firm's facts with `get_firm_facts`: its profile, plus the small-firm test and data gaps worked out in
   code, so the model never has to compare numbers itself.
4. Combines the section summaries and the firm's facts into a `Brief` for the firm.

The `Brief` (see `brief.py`) is the fixed output: an overall summary, then one item per obligation with an `id`, a
`summary` and a `source`. It comes back as `result["structured_response"]`.

`instructions.md` is the system prompt. Deep agents come with planning, a virtual filesystem and a `task` tool for
delegating to sub-agents, so the orchestrator can plan the work and fan the sections out on its own.
"""
from brief import Brief
from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from sub_agent import section_summariser_subagent
from tools.documents import get_document
from tools.firm_facts import get_firm_facts
from utils.models import PROJECT_ROOT, model

INSTRUCTIONS = (PROJECT_ROOT / "instructions.md").read_text()

def build_agent(**kwargs):
    """Build a runnable deep agent. Pass `checkpointer` and `store` to give it memory.
    """
    return create_deep_agent(
        model=init_chat_model(model),
        tools=[get_document, get_firm_facts],
        system_prompt=INSTRUCTIONS,
        subagents=[section_summariser_subagent],
        response_format=Brief,
        **kwargs,
    )

agent = build_agent()

# Example invoke:
# agent.invoke({"messages": [{"role": "user", "content": "Summarise document PS26-4 for FIRM-001"}]})
