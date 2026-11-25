"""Nodes for the card dispute graph.

Flow: load_case -> classify_dispute -> triage -> [fraud | merchant | friendly_fraud] specialist
      -> draft_evidence_pack <-> quality_review (max 2 rewrites) -> human_approval -> finalise
"""

from typing import Literal

from langchain.chat_models import init_chat_model
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from tools.cases import get_complaint, get_customer, get_dispute_history, get_merchant_history, get_transaction
from tools.memory import record_dispute
from utils.models import model
from utils.state import FRAUD_CODES, MyAgentState, ReasonCode, SendBackTo


def llm():
    return init_chat_model(model)


# ---------------------------------------------------------------- 1. load the case (owner: graph)
def load_case(state: MyAgentState) -> dict:
    complaint = get_complaint(state["complaint_id"])
    tx = get_transaction(complaint["transaction_id"])
    return {
        "complaint": complaint,
        "customer": get_customer(complaint["customer_id"]),
        "transaction": tx,
        "merchant_history": get_merchant_history(complaint["customer_id"], tx.get("merchant", ""), tx.get("transaction_id", "")),
        "dispute_history": get_dispute_history(complaint["customer_id"]),
    }


# ---------------------------------------------------------------- 2. classify (owner: graph; scored by evals)
class Classification(BaseModel):
    reason_code: ReasonCode = Field(description="The card scheme reason code that best fits the complaint")
    vulnerable_signals: list[str] = Field(description="Quotes suggesting the customer may be vulnerable (distress, financial hardship, illness). Empty if none.")


def classify_dispute(state: MyAgentState) -> dict:
    prompt = f"""Classify this card dispute complaint.

Complaint: {state["complaint"]["text"]}
Transaction: {state["transaction"]}{_review_feedback_for(state, "classify_dispute")}"""
    result = llm().with_structured_output(Classification, method="json_schema").invoke(prompt)
    return result.model_dump()


# ---------------------------------------------------------------- 3. triage (deterministic, so it is easy to test)
def triage(state: MyAgentState) -> dict:
    if state["reason_code"] not in FRAUD_CODES:
        return {"route": "merchant", "route_reasons": [f"Reason code {state['reason_code']} is a merchant dispute"]}

    tx = state["transaction"]
    reasons = []
    if state["merchant_history"]:
        reasons.append(f"{len(state['merchant_history'])} earlier undisputed purchase(s) at {tx['merchant']}")
    if tx.get("device_known") and tx.get("3ds_passed"):
        reasons.append("Made on the customer's known device and passed 3-D Secure")
    lost_fraud_claims = [d for d in state["dispute_history"] if d["reason_code"] in FRAUD_CODES and d["outcome"] == "lost"]
    if lost_fraud_claims:
        reasons.append(f"{len(lost_fraud_claims)} earlier fraud claim(s) lost")

    if len(reasons) >= 2:
        return {"route": "friendly_fraud", "route_reasons": reasons}
    return {"route": "fraud", "route_reasons": reasons or ["No signals that the customer made the payment"]}


def route_to_specialist(state: MyAgentState) -> Literal["fraud_specialist", "merchant_specialist", "friendly_fraud_specialist"]:
    return f"{state['route']}_specialist"


# ---------------------------------------------------------------- 4. specialists (owner: specialists)
class Assessment(BaseModel):
    assessment: str = Field(description="3-5 sentences of reasoning, citing the evidence")
    recommendation: Literal["file_chargeback", "refund_customer", "decline_claim"]


def _specialist(state: MyAgentState, role: str) -> dict:
    prompt = f"""You are a {role} at a UK bank's card disputes team.

Complaint: {state["complaint"]["text"]}
Reason code: {state["reason_code"]}
Transaction: {state["transaction"]}
Earlier purchases at this merchant: {state["merchant_history"] or "None"}
Customer's past disputes: {state["dispute_history"] or "None"}
Triage signals: {state["route_reasons"]}

Assess the claim and recommend an action. Use only the facts above.{_review_feedback_for(state, "specialist")}"""
    return llm().with_structured_output(Assessment, method="json_schema").invoke(prompt).model_dump()


def fraud_specialist(state: MyAgentState) -> dict:
    # TODO: add fraud-specific checks (geo mismatch, unknown device, failed 3DS, odd hour)
    return _specialist(state, "fraud investigator")


def merchant_specialist(state: MyAgentState) -> dict:
    # TODO: check cancellation dates, delivery windows, duplicate amounts
    return _specialist(state, "merchant dispute specialist")


def friendly_fraud_specialist(state: MyAgentState) -> dict:
    # TODO: be careful and fair: friendly fraud is often a family member, not deliberate
    return _specialist(state, "first-party (friendly) fraud analyst")


# ---------------------------------------------------------------- 5. evidence pack (owner: specialists)
def draft_evidence_pack(state: MyAgentState) -> dict:
    rewrite = ""
    if state.get("review_result") == "fail":
        rewrite = f"""
A quality reviewer rejected your previous draft. Fix every issue below and rewrite the whole pack.
Reviewer feedback: {state["review_feedback"]}
Previous draft:
{state["evidence_pack"]}
"""
    prompt = f"""Draft a chargeback evidence pack in markdown with these sections:
Summary, Reason code, Transaction details, Evidence, Customer history, Vulnerability, Recommendation,
Points for the reviewer (anything uncertain or missing that the human approving this should check).
{rewrite}
Customer: {state["customer"]}
Complaint: {state["complaint"]["text"]}
Reason code: {state["reason_code"]}
Transaction: {state["transaction"]}
Route: {state["route"]} ({state["route_reasons"]})
Specialist assessment: {state["assessment"]}
Recommendation: {state["recommendation"]}
Vulnerability signals: {state["vulnerable_signals"] or "None"}

Use only these facts."""
    return {"evidence_pack": llm().invoke(prompt).content}


# ---------------------------------------------------------------- 5b. quality review (owner: Pradeep)
MAX_REVIEW_RETRIES = 2  # send-backs allowed; after that the case goes to the human either way


class QualityReview(BaseModel):
    issues: list[str] = Field(description="Each checklist failure, quoting the problem text. Empty if it passes.")
    verdict: Literal["pass", "fail"] = Field(description="pass only if every checklist item passes")
    send_back_to: SendBackTo = Field(description="Where the case must go next (see the routing rules)")
    feedback: str = Field(description="Concrete instructions for whoever fixes it. Empty if it passes.")


def review_pack(case: dict, evidence_pack: str) -> QualityReview:
    """Check an evidence pack against the raw case data. `case` is the output of load_case."""
    prompt = f"""You are the quality reviewer for a UK bank's card disputes team. Check this evidence pack
before a member of staff uses it to approve or override the recommendation. Judge it only against the source data.

Checklist (the pack fails if ANY item fails):
1. Grounded: every fact (amounts, dates, merchant, device, 3-D Secure, history) matches the source data.
2. Nothing invented: no evidence, events, confirmations or documents that are not in the source data.
3. Reason code fits: the reason code matches what the customer actually complains about.
4. Recommendation matches the evidence: e.g. a "not me" claim with several signs that the customer made the payment
   (earlier purchases there, known device + 3-D Secure, lost fraud claims) should not be a chargeback.
5. Complete: it has Summary, Reason code, Transaction details, Evidence, Customer history, Vulnerability and
   Recommendation sections, each with real content.
6. Vulnerability handled: if the complaint or profile shows vulnerability (age, illness, distress, hardship, scam,
   confusion), the Vulnerability section says so and the case is handled with care, not marked "none".
7. No accusing tone: never says or implies the customer is lying; friendly fraud is often a household member.
8. Decision-ready: a human could approve or override from this pack alone.
Only fail a pack for problems that could mislead the human or harm the customer. These are NOT failures:
style or wording (e.g. "File a chargeback" vs file_chargeback), reasonable judgements drawn from the data
(e.g. rating a vulnerable customer's level as medium), and leaving out data that is empty or irrelevant.

Routing rules for send_back_to:
- pass -> human_approval
- wrong reason code (item 3) -> classify_dispute
- recommendation does not follow from the evidence (item 4) -> specialist
- write-up problems: wrong or invented facts, missing sections, vulnerability ignored, accusing tone -> draft_evidence_pack
- evidence the bank does not have and a rewrite cannot fix -> human_approval

Source data:
Customer: {case["customer"]}
Complaint: {case["complaint"]["text"]}
Transaction: {case["transaction"]}
Earlier purchases at this merchant: {case["merchant_history"] or "None"}
Customer's past disputes: {case["dispute_history"] or "None"}

Evidence pack:
{evidence_pack}"""
    return llm().with_structured_output(QualityReview, method="json_schema").invoke(prompt)


def quality_review(state: MyAgentState) -> dict:
    result = review_pack(state, state["evidence_pack"])
    retries = state.get("retry_count", 0)
    target, feedback = result.send_back_to, result.feedback
    if result.verdict == "pass":
        target = "human_approval"
    elif target != "human_approval" and retries >= MAX_REVIEW_RETRIES:
        target = "human_approval"
        feedback += f" (Still failing after {MAX_REVIEW_RETRIES} retries: the human must check this.)"
    if result.verdict == "fail" and result.issues:
        feedback = "Issues: " + "; ".join(result.issues) + ". " + feedback
    return {
        "review_result": result.verdict,
        "review_feedback": feedback,
        "send_back_to": target,
        "retry_count": retries + (target != "human_approval"),
    }


def route_after_review(state: MyAgentState) -> Literal[
    "human_approval", "draft_evidence_pack", "classify_dispute",
    "fraud_specialist", "merchant_specialist", "friendly_fraud_specialist",
]:
    if state["send_back_to"] == "specialist":
        return route_to_specialist(state)
    return state["send_back_to"]


def _review_feedback_for(state: MyAgentState, step: str) -> str:
    """The reviewer's feedback, if the reviewer sent the case back to this step."""
    if state.get("review_result") == "fail" and state.get("send_back_to") == step:
        return f"\n\nA quality reviewer sent this back to you. Take this into account: {state['review_feedback']}"
    return ""


# ---------------------------------------------------------------- 6. human approval + memory (owner: memory/HITL)
def human_approval(state: MyAgentState) -> dict:
    answer = interrupt({
        "complaint_id": state["complaint_id"],
        "recommendation": state["recommendation"],
        "vulnerable_customer": bool(state["vulnerable_signals"] or state["customer"].get("vulnerable")),
        "evidence_pack": state["evidence_pack"],
        "quality_review": {
            "result": state.get("review_result"),
            "retries": state.get("retry_count", 0),
            "outstanding_issues": state.get("review_feedback", "") if state.get("review_result") == "fail" else "",
        },
        "question": "Approve the recommendation? Reply with {'decision': 'approve' | 'override:<action>', 'notes': ...}",
    })
    return {"decision": answer["decision"], "reviewer_notes": answer.get("notes", "")}


def finalise(state: MyAgentState) -> dict:
    record_dispute(
        customer_id=state["complaint"]["customer_id"],
        transaction_id=state["transaction"]["transaction_id"],
        merchant=state["transaction"]["merchant"],
        reason_code=state["reason_code"],
        outcome="pending",
        notes=f"{state['recommendation']} ({state['decision']}). {state['reviewer_notes']}".strip(),
    )
    return {}
