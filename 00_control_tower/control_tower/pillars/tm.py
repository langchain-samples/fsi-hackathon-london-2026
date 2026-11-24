"""Transaction monitoring pillar: runs Marcus's project 02 agent with thresholds set by the customer's KYC rating.

What the control tower adds around the 02 agent, without editing it:
- `context_for` picks the run's `MyAgentContext` from the customer's KYC rating and the active policy (PS26-7 6.3).
- `merchant_hold` refers payments to high-risk merchants over the limit, even if the agent approved them (6.4).
- Every block or human referral is an alert; enough alerts in the window flag the customer for a KYC re-rating (6.5).
- When the agent pauses for a human, the case is parked in the shared review queue instead of blocking the run.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import BaseStore
from langgraph.types import Command

from control_tower import memory
from control_tower.contracts import TmDecision
from control_tower.loader import load_project
from control_tower.policy import PolicyParams
from control_tower.settings import to_gbp

FOLDER = "02_transaction_monitoring_agent"
_saver = InMemorySaver()
_agents: dict[int, object] = {}


def project():
    return load_project(FOLDER, "utils.transaction_processing")


def agent_for(store: BaseStore):
    """One compiled 02 agent per store, so its tools read and write the shared memory."""
    if id(store) not in _agents:
        _agents[id(store)] = project()["agent"].build_agent(checkpointer=_saver, store=store)
    return _agents[id(store)]


def context_for(user_id: str, rating: str | None, policy: PolicyParams):
    """The 02 agent's run settings for this customer. High-risk customers get the policy's stricter thresholds."""
    ctx = project()["utils.state"].MyAgentContext(user_id=user_id)
    if rating == "high":
        ctx = replace(ctx, confidence_threshold=policy.high_risk_confidence_threshold,
                      grey_zone=(policy.high_risk_review_score, ctx.grey_zone[1]))
    return ctx


def merchant_hold(tx: dict, policy: PolicyParams) -> str | None:
    if tx["category"] in policy.high_risk_merchant_categories and policy.high_risk_merchant_limit_gbp is not None:
        gbp = to_gbp(tx["amount"], tx["currency"])
        if gbp > policy.high_risk_merchant_limit_gbp:
            para = policy.citations.get("high_risk_merchant_limit_gbp", "")
            return f"£{gbp:,.0f} to a high-risk merchant category ({tx['category']}) needs a human (para {para})"
    return None


def count_recent_alerts(alerts: list[dict], now: str, window_days: int) -> int:
    end = datetime.fromisoformat(now)
    start = end - timedelta(days=window_days)
    return sum(1 for a in alerts if start <= datetime.fromisoformat(a["timestamp"]) <= end)


def _thread(user_id: str, tag: str) -> dict:
    return {"configurable": {"thread_id": f"tm-{user_id}-{tag}"}}


def _after_decision(store: BaseStore, tx: dict, policy: PolicyParams) -> dict:
    """Read the 02 agent's decision back, apply the merchant rule, record alerts and the refresh trigger."""
    user_id = tx["user_id"]
    item = store.get(("decisions", user_id), tx["transaction_id"])
    decision = TmDecision(**item.value).model_dump() if item else {"transaction_id": tx["transaction_id"],
                                                                   "final_decision": "unknown"}

    hold = merchant_hold(tx, policy) if decision.get("final_decision") == "approve" \
        and decision.get("decided_by") == "agent" else None
    if hold:
        memory.open_review(store, f"tm-hold:{tx['transaction_id']}", "tm", {
            "transaction_id": tx["transaction_id"], "why_review": [hold], "agent_decision": "approve",
            "question": "Policy hold: approve or block?"}, {"kind": "hold", "user_id": user_id})

    is_alert = decision.get("final_decision") == "block" or decision.get("decided_by") == "human" or bool(hold)
    if is_alert:
        memory.put_alert(store, user_id, {"transaction_id": tx["transaction_id"], "timestamp": tx["timestamp"],
                                          "decision": decision.get("final_decision"), "hold": hold})
    memory.log_event(store, "tm", f"{tx['transaction_id']} {tx['merchant']} {tx['amount']:,.0f} {tx['currency']}: "
                     f"{decision.get('final_decision', '?')} by {decision.get('decided_by', '?')}",
                     {"decision": decision, "alert": is_alert, "hold": hold})

    customer_id = memory.customer_for_user(user_id)
    if is_alert and customer_id and policy.alerts_for_kyc_refresh:
        n = count_recent_alerts(memory.alerts_for(store, user_id), tx["timestamp"], policy.alert_window_days)
        if n >= policy.alerts_for_kyc_refresh:
            para = policy.citations.get("alerts_for_kyc_refresh", "")
            memory.flag_kyc_refresh(store, customer_id,
                                    f"{n} alerts in {policy.alert_window_days} days (para {para})")
            memory.log_event(store, "tm", f"KYC refresh triggered for {customer_id}", {"alerts": n})
    return decision


def run_tm(user_id: str, store: BaseStore, policy: PolicyParams, transaction_ids: list[str] | None = None,
           reviewer_answers: dict | None = None, run_tag: str = "live") -> list[dict]:
    """Score a customer's transactions in order.

    With `reviewer_answers` (transaction_id -> answer) a paused decision is answered at once, which is what evals
    and scripted runs use. Without it, the first pause parks a review case and the run stops; `resume_tm` carries on
    from there with the remaining transactions.
    """
    p = project()
    stream = p["utils.transaction_processing"].transaction_stream(user_id)
    if transaction_ids:
        stream = [tx for tx in stream if tx["transaction_id"] in transaction_ids]
    customer_id = memory.customer_for_user(user_id)
    rating = (memory.get_customer_risk(store, customer_id) or {}).get("rating") if customer_id else None
    ctx = context_for(user_id, rating, policy)
    memory.log_event(store, "tm", f"Monitoring {user_id} (KYC rating {rating or 'unknown'})", {
        "confidence_threshold": ctx.confidence_threshold, "grey_zone": list(ctx.grey_zone)})
    return _process(store, policy, stream, ctx, reviewer_answers, run_tag)


def _process(store, policy, stream, ctx, reviewer_answers, tag) -> list[dict]:
    agent = agent_for(store)
    message = project()["utils.transaction_processing"].transaction_message
    decisions = []
    for i, tx in enumerate(stream):
        result = agent.invoke(message(tx["transaction_id"]), _thread(tx["user_id"], tag), context=ctx)
        while "__interrupt__" in result:
            question = result["__interrupt__"][0].value
            if reviewer_answers is None:
                memory.open_review(store, f"tm:{tx['transaction_id']}", "tm", question, {
                    "kind": "graph", "user_id": tx["user_id"], "remaining": [t["transaction_id"] for t in stream[i + 1:]], "tag": tag,
                    "context": {"confidence_threshold": ctx.confidence_threshold, "grey_zone": list(ctx.grey_zone)}})
                memory.log_event(store, "tm", f"{tx['transaction_id']} referred to a human", question)
                return decisions
            answer = reviewer_answers.get(tx["transaction_id"], {"decision": question["agent_decision"]})
            result = agent.invoke(Command(resume=answer), _thread(tx["user_id"], tag), context=ctx)
        decisions.append(_after_decision(store, tx, policy))
    return decisions


def resume_tm(store: BaseStore, case: dict, answer: dict, policy: PolicyParams) -> list[dict]:
    """Apply a reviewer's answer to a parked TM case, then carry on with the customer's remaining transactions."""
    resume = case["resume"]
    memory.close_review(store, case["case_id"], answer)
    user_id = resume["user_id"]
    tx_id = case["case_id"].split(":", 1)[1]
    if resume["kind"] == "hold":
        memory.log_event(store, "tm", f"Reviewer {answer['decision']}d held payment {tx_id}", answer)
        return []
    ctx = replace(project()["utils.state"].MyAgentContext(user_id=user_id),
                  confidence_threshold=resume["context"]["confidence_threshold"],
                  grey_zone=tuple(resume["context"]["grey_zone"]))
    agent_for(store).invoke(Command(resume=answer), _thread(user_id, resume["tag"]), context=ctx)
    tx = next(t for t in project()["utils.transaction_processing"].transaction_stream(user_id)
              if t["transaction_id"] == tx_id)
    decisions = [_after_decision(store, tx, policy)]
    remaining = [t for t in project()["utils.transaction_processing"].transaction_stream(user_id)
                 if t["transaction_id"] in resume["remaining"]]
    return decisions + _process(store, policy, remaining, ctx, None, resume["tag"])
