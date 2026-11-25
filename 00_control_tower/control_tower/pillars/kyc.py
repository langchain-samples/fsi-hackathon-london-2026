"""KYC pillar: runs Nam's project 01 graph, then applies the active policy on top and writes the rating.

The 01 graph screens names against the sanctions and PEP lists. PS26-7 asks for more: close associates and family
members of PEPs that the customer mentions in their application (4.3), high-risk countries (4.2) and source of funds
for large deposits (4.4). `policy_overlay` adds those rules without changing Nam's graph, so his graph can be edited
freely. If his graph starts returning "medium" or its own reasons, they are kept and merged.
"""
from __future__ import annotations

import uuid

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import BaseStore
from langgraph.types import Command
from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from control_tower import memory
from control_tower.contracts import KycOutput, kyc_reasons
from control_tower.loader import load_project
from control_tower.policy import PolicyParams
from control_tower.settings import MODEL, to_gbp

FOLDER = "01_kyc_agent"
_ORDER = {"low": 0, "medium": 1, "high": 2}
_saver = InMemorySaver()
_graphs: dict[int, object] = {}


def graph(store: BaseStore | None = None):
    """Nam's 01 graph, compiled with the shared store so its nodes can read the live policy if they want to."""
    store = store or memory.get_store()
    if id(store) not in _graphs:
        _graphs[id(store)] = load_project(FOLDER)["agent"].build_agent(checkpointer=_saver, store=store)
    return _graphs[id(store)]


class ConnectedPerson(BaseModel):
    name: str = Field(description="The person's full name as written")
    relationship: str = Field(description="How the applicant says they are connected, e.g. 'business partner'")


class StatedFacts(BaseModel):
    """Facts the applicant states about themselves that name screening does not look at."""

    connected_people: list[ConnectedPerson] = Field(
        description="Every other named person the applicant says they are connected to (family, business)")
    country_of_residence: str = Field(description="The country the applicant lives in")
    initial_deposit_amount: float | None = Field(description="The first deposit they expect, as a number")
    initial_deposit_currency: str | None = Field(description="ISO currency code of that deposit, e.g. 'GBP'")


def extract_facts(application: str, model: str = MODEL) -> StatedFacts:
    extractor = init_chat_model(model).with_structured_output(StatedFacts, method="json_schema")
    return extractor.invoke(f"Extract these facts from the account application.\n\n{application}")


def policy_overlay(profile: dict, facts: StatedFacts | None, policy: PolicyParams, check_pep) -> list[dict]:
    """Reasons the active policy adds, each as {level, text, paragraph}. Pure function: easy to test."""
    reasons: list[dict] = []
    cite = policy.citations

    if facts and policy.edd_for_pep_family_and_associates:
        for person in facts.connected_people:
            for hit in check_pep(person.name):
                reasons.append({
                    "level": "high",
                    "text": f"Stated {person.relationship} of PEP {hit['matched_name']} ({hit['details']}): EDD required",
                    "paragraph": cite.get("edd_for_pep_family_and_associates", ""),
                })

    countries = [c.lower() for c in policy.high_risk_countries]
    if countries:
        nationality = (profile.get("nationality") or "").lower()
        residence = (facts.country_of_residence if facts else "").lower()
        for country in countries:
            if country[:6] in nationality or country in residence:
                reasons.append({"level": "high", "text": f"Linked to high-risk third country {country.title()}",
                                "paragraph": cite.get("high_risk_countries", "")})

    if facts and policy.source_of_funds_threshold_gbp and facts.initial_deposit_amount:
        gbp = to_gbp(facts.initial_deposit_amount, facts.initial_deposit_currency or "GBP")
        if gbp > policy.source_of_funds_threshold_gbp:
            reasons.append({"level": "medium",
                            "text": f"Initial deposit about £{gbp:,.0f}: source-of-funds evidence needed",
                            "paragraph": cite.get("source_of_funds_threshold_gbp", "")})
    return reasons


def _needs_facts(policy: PolicyParams) -> bool:
    return policy.edd_for_pep_family_and_associates or bool(policy.high_risk_countries) \
        or policy.source_of_funds_threshold_gbp is not None


def run_kyc(customer_id: str, store: BaseStore, policy: PolicyParams, model: str = MODEL,
            facts_extractor=extract_facts) -> dict:
    """Onboard or re-rate one customer. Returns the rating record that was written to the shared store."""
    project = load_project(FOLDER)
    thread_id = f"kyc-{customer_id}-{uuid.uuid4().hex[:6]}"
    config = {"configurable": {"thread_id": thread_id}}
    memory.log_event(store, "kyc", f"Screening {customer_id}", {"policy": policy.source_document})

    result = graph(store).invoke({"customer_id": customer_id}, config)
    state = KycOutput(**graph(store).get_state(config).values)  # fails loudly if 01's output shape breaks
    paused = "__interrupt__" in result

    reasons = kyc_reasons(state)
    facts = facts_extractor(state.application, model) if _needs_facts(policy) else None
    reasons += policy_overlay(state.profile.model_dump(), facts, policy, project["tools.screening"].check_pep)

    rating = max([state.risk_level, *(r["level"] for r in reasons)], key=lambda l: _ORDER.get(l, 0))
    previous = memory.get_customer_risk(store, customer_id)
    record = {
        "customer_id": customer_id,
        "name": state.profile.full_name,
        "user_id": memory.user_for_customer(customer_id),
        "rating": rating,
        "reasons": reasons or [{"level": "low", "text": "No watchlist matches and no policy triggers",
                                "paragraph": ""}],
        "policy": policy.source_document,
        "kyc_thread": thread_id,
        "decision": state.decision,
        "status": "approved" if state.decision == "approve" else "pending_review",
        # Keep the last *different* rating, so the portal can show "was LOW under baseline".
        "previous": (previous.get("previous") if previous and previous["rating"] == rating
                     else {"rating": previous["rating"], "policy": previous["policy"]} if previous else None),
    }

    if paused:
        question = result["__interrupt__"][0].value
        memory.open_review(store, f"kyc:{customer_id}", "kyc", {**question, "policy_reasons": reasons},
                           {"kind": "graph", "thread_id": thread_id})
    elif rating == "high" and record["decision"] == "approve":
        # The 01 graph auto-approved, but the policy says EDD: hold it for senior-manager sign-off (PS26-7 4.6).
        record.update(status="pending_review", decision=None)
        memory.open_review(store, f"kyc:{customer_id}", "kyc", {
            "customer": record["name"], "risk_reasons": [r["text"] for r in reasons],
            "question": "EDD sign-off: approve or reject this customer?", "policy_reasons": reasons,
        }, {"kind": "edd"})

    memory.put_customer_risk(store, customer_id, record)
    memory.close_refresh_flag(store, customer_id)
    was = f" (was {previous['rating'].upper()} under {previous['policy']})" \
        if previous and previous["rating"] != rating else ""
    paragraphs = ", ".join(dict.fromkeys(r["paragraph"] for r in reasons if r["level"] == rating and r["paragraph"]))
    waiting = "; waiting for a human decision" if record["status"] == "pending_review" else ""
    memory.record_client(store, customer_id, f"KYC: rated {rating.upper()} under {policy.source_document}{was}{waiting}",
                         "kyc", paragraphs, name=record["name"], assessed_policy=policy.source_document,
                         due_changes=[], **({"onboarded": True} if record["status"] == "approved" else {}))
    memory.log_event(store, "kyc", f"{record['name']} rated {rating.upper()}",
                     {"customer_id": customer_id, "reasons": record["reasons"], "status": record["status"]})
    return record


def resume_kyc(store: BaseStore, case: dict, answer: dict) -> dict:
    """Apply a reviewer's answer to a parked KYC case."""
    customer_id = case["case_id"].split(":", 1)[1]
    record = memory.get_customer_risk(store, customer_id) or {}
    if case["resume"]["kind"] == "graph":
        config = {"configurable": {"thread_id": case["resume"]["thread_id"]}}
        graph(store).invoke(Command(resume=answer), config)
        state = graph(store).get_state(config).values
        record.update(decision=state.get("decision"), memo=state.get("decision_memo"))
    else:
        record.update(decision=answer["decision"])
    record.update(status="approved" if record.get("decision") == "approve" else "rejected",
                  reviewer_notes=answer.get("notes", ""))
    memory.put_customer_risk(store, customer_id, record)
    memory.close_review(store, case["case_id"], answer)
    memory.record_client(store, customer_id, f"KYC: reviewer decision recorded, customer {record['status']}", "kyc",
                         **({"onboarded": True} if record["status"] == "approved" else {}))
    memory.log_event(store, "kyc", f"Reviewer {record['status']} {record.get('name', customer_id)}", answer)
    return record
