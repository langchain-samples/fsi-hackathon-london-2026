from pydantic import BaseModel, Field
from typing import Literal

from langchain.chat_models import init_chat_model
from langgraph.types import interrupt

from tools.applications import get_application
from tools.screening import check_pep, check_sanctions

from utils.models import model
from utils.state import MyAgentState

#
# Customer Profile object definition to map LLM output into
#
class CustomerProfile(BaseModel):
    """Identity details from a customer's account application.

    Application forms are free text, but screening needs clean, named fields. This schema is handed to
    `model.with_structured_output` so the LLM extracts the customer's identity details into a typed object
    that screen_customer can read reliably (name and date of birth are matched against the watchlists).
    """
    full_name: str = Field(description="The customer's full name, exactly as written")
    date_of_birth: str = Field(description="Date of birth in YYYY-MM-DD format")
    nationality: str = Field(description="Country of citizenship, e.g. 'British'")
    occupation: str = Field(description="Job title or role")
    source_of_funds: str = Field(description="Where the money they will deposit comes from")

#
# Nodes for the agent Graph
#
def extract_profile(state: MyAgentState) -> dict:
    """Turn the customer's free-text application into structured identity details.

    Takes in: `customer_id` from the state.
    Does: looks up the raw application text, then asks the LLM (via `extractor`) to fill in a CustomerProfile.
    Returns: `application` (the raw text) and `profile` (the extracted fields as a plain dict).
    """
    application = get_application(state["customer_id"])

    extractor = init_chat_model(model).with_structured_output(CustomerProfile, method="json_schema")
    profile = extractor.invoke(f"Extract the customer's details from this account application.\n\n{application}")

    return {"application": application, "profile": profile.model_dump()}


def screen_customer(state: MyAgentState) -> dict:
    """Check the customer against the sanctions and PEP watchlists.

    Takes in: `profile` from the state (uses `full_name` and `date_of_birth`).
    Does: runs the fuzzy-match sanctions check (name plus date of birth) and the PEP check (name only).
    Returns: `screening_hits`, a list of every watchlist entry that matched (empty if the customer is clean).
    """
    name = state["profile"]["full_name"]
    hits = check_sanctions(name, state["profile"]["date_of_birth"]) + check_pep(name)
    return {"screening_hits": hits}


def assess_risk(state: MyAgentState) -> dict:
    """Decide whether the customer is low or high risk from the screening results.

    Takes in: `screening_hits` from the state.
    Does: any watchlist hit makes the customer high risk; no hits makes them low risk. Each hit is turned
    into a readable reason for the audit trail.
    Returns: `risk_level` ("low" or "high") and `risk_reasons` (the list of reasons behind that level).
    """
    reasons = [
        f"{hit['list'].upper()} match: {hit['matched_name']} ({hit['details']}), similarity {hit['score']}"
        for hit in state["screening_hits"]
    ]
    if reasons:
        return {"risk_level": "high", "risk_reasons": reasons}
    return {"risk_level": "low", "risk_reasons": ["No watchlist matches"]}


def route_on_risk(state: MyAgentState) -> Literal["auto_approve", "human_review"]:
    """Choose which branch the graph takes after the risk assessment.

    Takes in: `risk_level` from the state.
    Does: sends high-risk customers to a human and everything else to automatic approval.
    Returns: the name of the next node, "human_review" or "auto_approve". It updates no state keys, because it
    is a routing function and not a node.
    """
    if state["risk_level"] == "high":
        return "human_review"
    return "auto_approve"


def human_review(state: MyAgentState) -> dict:
    """Pause the graph so a compliance officer can decide on a high-risk customer.

    Takes in: `profile` (for the customer's name) and `risk_reasons` from the state.
    Does: calls `interrupt()`, which stops the graph and hands the reviewer a question. The graph waits until
    it is resumed with an answer shaped like {"decision": ..., "notes": ...}, which becomes the return value
    of `interrupt()`.
    Returns: `decision` (the reviewer's choice), `reviewer` ("human") and `reviewer_notes` (their notes).
    """
    answer = interrupt(
        {
            "customer": state["profile"]["full_name"],
            "risk_reasons": state["risk_reasons"],
            "question": "Approve or reject this customer? Reply with {'decision': ..., 'notes': ...}",
        }
    )
    return {"decision": answer["decision"], "reviewer": "human", "reviewer_notes": answer.get("notes", "")}


def auto_approve(state: MyAgentState) -> dict:
    """Approve a low-risk customer without involving a human.

    Takes in: the state, but reads nothing from it; this branch is only reached when `risk_level` is "low".
    Does: records an automatic approval.
    Returns: `decision` ("approve"), `reviewer` ("system") and `reviewer_notes` explaining the approval.
    """
    return {"decision": "approve", "reviewer": "system", "reviewer_notes": "Low risk, approved automatically."}


def write_decision(state: MyAgentState) -> dict:
    """Write the short decision memo that goes in the audit file.

    Takes in: `profile`, `screening_hits`, `risk_level`, `decision`, `reviewer` and `reviewer_notes`.
    Does: gives those facts to the LLM and asks for a memo under 120 words, using only what it was given.
    Returns: `decision_memo`, the memo text.
    """
    prompt = f"""
You are a KYC compliance analyst. Write a short decision memo (under 120 words) for the audit file.

Customer profile: {state["profile"]}
Screening hits: {state["screening_hits"] or 'None'}
Risk level: {state["risk_level"]}
Decision: {state["decision"]} (by {state["reviewer"]})
Reviewer notes: {state["reviewer_notes"]}

Say who the customer is, what screening found, the risk level, and the final decision with its reason.
Use only the facts above."""

    return {"decision_memo": init_chat_model(model).invoke(prompt).content}
