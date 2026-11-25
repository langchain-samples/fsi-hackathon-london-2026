"""The supervisor deep agent: one agent over the three pillars, sharing one store.

The supervisor works from skills, not sub-agents. Each skill in `agent_home/skills/` is the playbook for one stage
of a client's lifecycle (onboard, monitor, regulatory change, KYC refresh, briefing). The agent sees the skill
names and descriptions, and reads a playbook only when the request calls for it.

All three pillars run through tools, each calling the owner's agent unchanged: KYC (project 01, a LangGraph graph),
TM (project 02, a LangChain agent) and regulatory (project 03, a deep agent with parallel sub-agents). Their runs
nest inside the supervisor's trace.

The supervisor reads the client file but never writes it, and it has no tool to answer a review: human decisions
are recorded only through the review inbox.

Run it in Studio with `uv run langgraph dev` from this folder, or call `build_supervisor()` from Python.
"""
from __future__ import annotations

from pathlib import Path

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.chat_models import init_chat_model
from langchain.tools import ToolRuntime, tool

from control_tower import memory
from control_tower.pillars import kyc, reg, tm
from control_tower.settings import MODEL

HERE = Path(__file__).resolve().parents[1]
INSTRUCTIONS = (HERE / "instructions.md").read_text()
AGENT_HOME = HERE / "agent_home"   # the only folder the agent can see; holds skills/


def _store(runtime: ToolRuntime):
    return runtime.store or memory.get_store()


def _client(store, customer_id: str) -> dict | None:
    """The client file with the stage worked out now, in case something changed since the last write."""
    client = memory.get_client(store, customer_id)
    return {**client, "stage": memory.client_stage(store, customer_id)} if client else None


@tool(parse_docstring=True)
def brief_regulation(document_id: str, runtime: ToolRuntime, firm_id: str = memory.DEFAULT_FIRM) -> dict:
    """Brief a regulatory document with the regulatory pillar and park the control changes it implies for approval.

    The policy does not go live here. It waits as a review case until a human approves it in the review inbox.

    Args:
        document_id: The document to brief, for example "PS26-7".
        firm_id: The firm to brief it for.
    """
    store = _store(runtime)
    result = reg.run_reg(document_id, firm_id, store)
    brief = next((e["detail"]["brief"] for e in reversed(memory.events(store))
                  if e["title"] == f"Brief ready for {document_id}"), "")
    return {**result, "brief": brief}


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
    """The client file and everything else shared memory holds on one customer.

    Returns the lifecycle stage, the dated history with paragraph citations, the policy the last assessment was made
    under, any control changes still to be applied, the KYC rating and reasons, alerts and open reviews.

    Args:
        customer_id: The KYC customer ID, for example "CUST-026".
    """
    store = _store(runtime)
    user_id = memory.user_for_customer(customer_id)
    return {"client": _client(store, customer_id) or f"No client file: {customer_id} has not been onboarded",
            "live_policy": memory.active_policy_id(store),
            "risk": memory.get_customer_risk(store, customer_id),
            "alerts": memory.alerts_for(store, user_id) if user_id else [],
            "open_reviews": [{"case_id": r["case_id"], "question": r["question"].get("question")}
                             for r in memory.reviews_for_customer(store, customer_id)]}


def _summary(store, client: dict) -> dict:
    risk = memory.get_customer_risk(store, client["customer_id"]) or {}
    return {"customer_id": client["customer_id"], "name": client.get("name"),
            "stage": memory.client_stage(store, client["customer_id"]), "rating": risk.get("rating"),
            "assessed_policy": client["assessed_policy"]}


@tool
def client_register(runtime: ToolRuntime) -> dict:
    """Every client the control tower knows, one line each: lifecycle stage, KYC rating, policy assessed under."""
    store = _store(runtime)
    return {"live_policy": memory.active_policy_id(store), "clients": [_summary(store, c) for c in memory.clients(store)]}


@tool
def affected_clients(runtime: ToolRuntime) -> dict:
    """Clients last assessed under an older policy than the live one, with the control changes they are due."""
    store = _store(runtime)
    due = [{**_summary(store, c), "due_changes": c["due_changes"]} for c in memory.clients(store)
           if c["assessed_policy"] != memory.active_policy_id(store)]
    return {"live_policy": memory.active_policy_id(store), "reassessment_due": due}


@tool
def open_reviews(runtime: ToolRuntime) -> list[dict]:
    """Every case waiting for a human, across all pillars."""
    return [{"case_id": r["case_id"], "pillar": r["pillar"], "question": r["question"].get("question")}
            for r in memory.reviews(_store(runtime))]


TOOLS = [brief_regulation, onboard_customer, monitor_customer, process_kyc_refresh, customer_view,
         client_register, affected_clients, open_reviews]


def build_supervisor(model=None, **kwargs):
    return create_deep_agent(
        model=model or init_chat_model(MODEL),
        tools=TOOLS,
        system_prompt=INSTRUCTIONS,
        skills=["/skills/"],
        backend=FilesystemBackend(root_dir=AGENT_HOME, virtual_mode=True),
        permissions=[FilesystemPermission(operations=["write"], paths=["/**"], mode="deny")],  # skills are read-only
        name="control-tower",
        **kwargs,
    )


agent = build_supervisor()
