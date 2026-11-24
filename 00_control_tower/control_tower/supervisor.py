"""The supervisor deep agent: one agent over the three pillars, sharing one store.

- The regulatory pillar (project 03, itself a deep agent with parallel sub-agents) is a `CompiledSubAgent`, so its
  whole run nests inside the supervisor's trace.
- KYC (project 01, a LangGraph graph) and TM (project 02, a LangChain agent) run through tools, because they take
  structured inputs and park human reviews in the shared queue rather than chatting.

Run it in Studio with `uv run langgraph dev` from this folder, or call `build_supervisor()` from Python.
"""
from __future__ import annotations

from pathlib import Path

from deepagents import CompiledSubAgent, create_deep_agent
from langchain.chat_models import init_chat_model
from langchain.tools import ToolRuntime, tool

from control_tower import memory
from control_tower.pillars import kyc, reg, tm
from control_tower.settings import MODEL

INSTRUCTIONS = (Path(__file__).resolve().parents[1] / "instructions.md").read_text()


def _store(runtime: ToolRuntime):
    return runtime.store or memory.get_store()


@tool(parse_docstring=True)
def publish_policy(document_id: str, brief: str, runtime: ToolRuntime, firm_id: str = memory.DEFAULT_FIRM) -> dict:
    """Turn a regulatory brief into control changes for KYC and transaction monitoring, for human approval.

    Args:
        document_id: The document the brief is for, for example "PS26-7".
        brief: The regulatory-analyst's brief, in full.
        firm_id: The firm the brief was written for.
    """
    return reg.publish_policy_from_brief(document_id, brief, firm_id, _store(runtime))


@tool(parse_docstring=True)
def onboard_customer(customer_id: str, runtime: ToolRuntime) -> dict:
    """Screen and risk-rate a customer under the live policy, and record the rating in shared memory.

    Args:
        customer_id: The KYC customer ID, for example "CUST-026".
    """
    store = _store(runtime)
    return kyc.run_kyc(customer_id, store, reg.active_policy(store))


@tool(parse_docstring=True)
def monitor_customer(customer_id: str, runtime: ToolRuntime) -> dict:
    """Score a customer's card transactions, using thresholds set by their KYC rating and the live policy.

    Args:
        customer_id: The KYC customer ID, for example "CUST-026".
    """
    store = _store(runtime)
    user_id = memory.user_for_customer(customer_id)
    if not user_id:
        return {"error": f"{customer_id} has no card account linked in customer_links.jsonl"}
    decisions = tm.run_tm(user_id, store, reg.active_policy(store))
    return {"decisions": decisions, "alerts": memory.alerts_for(store, user_id),
            "waiting_for_review": [r["case_id"] for r in memory.reviews(store) if r["pillar"] == "tm"]}


@tool
def process_kyc_refresh(runtime: ToolRuntime) -> list[dict]:
    """Re-rate every customer that transaction monitoring has flagged for a KYC refresh."""
    store = _store(runtime)
    return [kyc.run_kyc(flag["customer_id"], store, reg.active_policy(store))
            for flag in memory.open_refresh_flags(store)]


@tool(parse_docstring=True)
def customer_view(customer_id: str, runtime: ToolRuntime) -> dict:
    """Everything the shared memory holds on one customer: rating, reasons, alerts and open reviews.

    Args:
        customer_id: The KYC customer ID, for example "CUST-026".
    """
    store = _store(runtime)
    user_id = memory.user_for_customer(customer_id)
    return {"risk": memory.get_customer_risk(store, customer_id),
            "alerts": memory.alerts_for(store, user_id) if user_id else [],
            "open_reviews": [r for r in memory.reviews(store) if customer_id in r["case_id"]]}


@tool
def open_reviews(runtime: ToolRuntime) -> list[dict]:
    """Every case waiting for a human, across all pillars."""
    return [{"case_id": r["case_id"], "pillar": r["pillar"], "question": r["question"].get("question")}
            for r in memory.reviews(_store(runtime))]


TOOLS = [publish_policy, onboard_customer, monitor_customer, process_kyc_refresh, customer_view, open_reviews]


def build_supervisor(model=None, **kwargs):
    regulatory_analyst = CompiledSubAgent(
        name="regulatory-analyst",
        description=("Briefs a regulatory document for a firm, section by section, with obligations, deadlines and "
                     "verbatim evidence. Ask: 'Summarise document <ID> for <FIRM-ID>'."),
        runnable=reg.reg_agent(),
    )
    return create_deep_agent(
        model=model or init_chat_model(MODEL),
        tools=TOOLS,
        system_prompt=INSTRUCTIONS,
        subagents=[regulatory_analyst],
        name="control-tower",
        **kwargs,
    )


agent = build_supervisor()
