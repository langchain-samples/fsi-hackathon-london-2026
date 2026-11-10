from langchain.chat_models import init_chat_model
from langchain.agents import create_agent

from tools.customers import get_customer_profile
from tools.decisions import record_decision
from tools.transactions import get_transaction

from utils.models import PROJECT_ROOT, model
from utils.state import MyAgentContext

#
# LangChain Agent Definition
#
def build_agent(**kwargs):
    """Build a runnable agent with `create_agent`. Pass `checkpointer` and `store` to give it memory.
    """
    agent = create_agent(
        model=init_chat_model(model),
        tools=[get_customer_profile, get_transaction, record_decision],
        system_prompt=(PROJECT_ROOT / "instructions.md").read_text(),
        context_schema=MyAgentContext,
        **kwargs
    )

    return agent

agent = build_agent()

# Example invoke:
# agent.invoke({ "messages": [{"role": "user", "content": "New transaction: {...} "}] }, context=MyAgentContext(user_id="USR-001"))
