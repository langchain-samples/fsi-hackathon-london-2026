"""Transaction monitoring pillar: runs Marcus's project 02 graph with the cut-offs set by the client's KYC rating.

02 does the work in nine steps (rules triage in code, an investigator and an auditor model, a person for every alert;
see 02's README). The control tower adds around it, without editing it:
- `context_for` builds the run's `MyAgentContext` from the KYC record and the live policy: the KYC rating picks the
  rules, and high-risk clients get the policy's cut-offs (medium from 20 under PS26-7, 6.3).
- When 02 pauses for a person, the case is parked in the shared review queue instead of blocking the run.
- Every block or human referral is an alert (3.5); enough alerts in the window flag the client for a KYC re-rating
  (6.5), due within 10 business days (5.5), and the TM conduct score is saved for the refresh reviewer.
"""
from __future__ import annotations

from dataclasses import asdict, fields, replace
from datetime import date, datetime, timedelta

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
    """One compiled 02 graph per store, so its nodes read and write the shared memory."""
    if id(store) not in _agents:
        _agents[id(store)] = project()["agent"].build_agent(checkpointer=_saver, store=store)
    return _agents[id(store)]


def context_for(user_id: str, rating: str | None, policy: PolicyParams, reasons: list[str] | None = None):
    """The 02 run settings for this client. High-risk clients get the policy's stricter cut-offs and threshold."""
    ctx = project()["utils.state"].MyAgentContext(user_id=user_id, kyc_rating=rating,
                                                  kyc_reasons=tuple(reasons or ()),
                                                  policy_version=policy.source_document)
    if rating == "high":
        ctx = replace(ctx, confidence_threshold=policy.high_risk_confidence_threshold,
                      grey_zone=(policy.high_risk_review_score, ctx.grey_zone[1]))
    return ctx


def _context_record(ctx) -> dict:
    return {k: list(v) if isinstance(v, tuple) else v for k, v in asdict(ctx).items()}


def _context_from(record: dict):
    cls = project()["utils.state"].MyAgentContext
    known = {f.name for f in fields(cls)}
    values = {k: tuple(v) if isinstance(v, list) else v for k, v in record.items() if k in known}
    return cls(**values)


def triage_preview(user_id: str, ctx) -> dict[str, dict]:
    """What the rules engine says about each of the client's payments under these settings. Code only, no model:
    the portal shows it before monitoring runs, so the bands are visible at once."""
    rules = project()["tools.rules"]
    return {tx["transaction_id"]: rules.triage(tx, ctx.kyc_rating, tuple(ctx.grey_zone), ctx.rule_set)
            for tx in project()["utils.transaction_processing"].transaction_stream(user_id)}


def rule_sets() -> list[dict]:
    return project()["tools.rules"].rule_sets()


def merchant_hold(tx: dict, policy: PolicyParams) -> str | None:
    """A backstop for 6.4 if a payment is ever approved by a model alone. 02's rule R-03 normally catches these."""
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


def business_days_after(day: date, n: int) -> date:
    """`n` business days after `day`, skipping weekends (3.6; public holidays are not modelled)."""
    while n:
        day += timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


def conduct_score(store: BaseStore, user_id: str) -> dict:
    """The TM conduct score for the period monitored: evidence for the KYC reviewer, never a rating on its own.

    Red: a block confirmed by a reviewer, an MLRO referral, or a 6.5 trigger. Amber: explained alerts that recur, or
    a request for information still open. Green: no alerts, or each explained once.
    """
    decisions = sorted((i.value for i in store.search(("decisions", user_id), limit=1000)),
                       key=lambda d: d.get("timestamp", d["transaction_id"]))
    customer_id = memory.customer_for_user(user_id)
    red, amber = [], []
    for d in decisions:
        if d["decided_by"] == "human" and d["final_decision"] == "block":
            red.append(f"{d['transaction_id']} blocked by a reviewer"
                       + (" and referred to the MLRO (7.1)" if d.get("next_step") == "refer_mlro" else ""))
        elif d.get("next_step") == "request_information" and not d.get("reviewer_note"):
            amber.append(f"{d['transaction_id']}: information requested from the client, no answer on file")
    explained = [d["transaction_id"] for d in decisions if d["decided_by"] == "human" and d["final_decision"] == "approve"
                 and d.get("reviewer_note")]
    if len(explained) >= 2:
        amber.append(f"{len(explained)} alerts explained by reviewers ({', '.join(explained)}): recurring")
    flag = next((f for f in memory.open_refresh_flags(store) if f["customer_id"] == customer_id), None)
    if flag:
        red.append(f"KYC refresh triggered: {flag['reason']}")
    alerts = memory.alerts_for(store, user_id)
    score = "red" if red else "amber" if amber else "green"
    drivers = red + amber or [f"{len(alerts)} alert(s), each explained once" if alerts else "No alerts"]
    return {"score": score, "drivers": drivers, "alerts": len(alerts), "decisions": len(decisions),
            "period": [decisions[0].get("timestamp", "")[:10], decisions[-1].get("timestamp", "")[:10]]
            if decisions else None}


def start_replay(store: BaseStore, user_id: str) -> None:
    """Forget a customer's earlier monitoring run, so the same transactions can be replayed under a new policy.

    Clears the alerts, and the 02 graph's own decisions and reviewer notes. Without this the reviewer's notes from
    the first run (for example "booked a flight to a conference") would reach the investigator on the replay, and the
    before/after comparison would stop being like for like.
    """
    for namespace in (("alerts", user_id), ("decisions", user_id), ("customer_notes", user_id)):
        for item in store.search(namespace, limit=1000):
            store.delete(namespace, item.key)


def _thread(user_id: str, tag: str) -> dict:
    return {"configurable": {"thread_id": f"tm-{user_id}-{tag}"}}


def _after_decision(store: BaseStore, tx: dict, policy: PolicyParams) -> dict:
    """Read 02's decision back, record alerts, and raise the KYC refresh when alerts repeat."""
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

    # 3.5: an alert is any payment monitoring blocks or refers for human review.
    is_alert = decision.get("final_decision") == "block" or decision.get("decided_by") == "human" or bool(hold)
    band, rules = decision.get("band"), decision.get("rules_fired") or []
    how = f"band {band}" + (f" ({', '.join(rules)})" if rules else "") if band else ""
    if is_alert:
        memory.put_alert(store, user_id, {"transaction_id": tx["transaction_id"], "timestamp": tx["timestamp"],
                                          "decision": decision.get("final_decision"), "hold": hold, "band": band,
                                          "rules_fired": rules, "next_step": decision.get("next_step")})
    memory.log_event(store, "tm", f"{tx['transaction_id']} {tx['merchant']} {tx['amount']:,.0f} {tx['currency']}: "
                     + (f"{how}, " if how else "") + f"{decision.get('final_decision', '?')} by "
                     f"{decision.get('decided_by', '?')}" + (f", audit {decision['audit']}" if decision.get("audit") else ""),
                     {"decision": decision, "alert": is_alert, "hold": hold})

    customer_id = memory.customer_for_user(user_id)
    if is_alert and customer_id:
        memory.record_client(store, customer_id, f"TM: alert on {tx['transaction_id']} ({tx['merchant']}, "
                             f"{tx['amount']:,.0f} {tx['currency']})" + (f", {how}" if how else "") + ": "
                             f"{decision.get('final_decision')} by {decision.get('decided_by', '?')}"
                             + ("; policy hold" if hold else ""), "tm", "3.5")
    if is_alert and customer_id and policy.alerts_for_kyc_refresh:
        n = count_recent_alerts(memory.alerts_for(store, user_id), tx["timestamp"], policy.alert_window_days)
        if n >= policy.alerts_for_kyc_refresh:
            para = policy.citations.get("alerts_for_kyc_refresh", "")
            due = business_days_after(datetime.fromisoformat(tx["timestamp"]).date(), 10).isoformat()
            memory.flag_kyc_refresh(store, customer_id, f"{n} alerts in {policy.alert_window_days} days (para {para})",
                                    due_by=due, alerts=n, trigger=tx["transaction_id"])
            memory.put_tm_summary(store, customer_id, {**conduct_score(store, user_id), "customer_id": customer_id,
                                                       "opened_by": tx["transaction_id"], "due_by": due})
            memory.record_client(store, customer_id, f"TM: {n} alerts in {policy.alert_window_days} days, KYC "
                                 f"refresh flagged, due by {due} (para 5.5)", "tm", para)
            memory.log_event(store, "tm", f"KYC refresh triggered for {customer_id}, due by {due}", {"alerts": n})
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
    risk = (memory.get_customer_risk(store, customer_id) or {}) if customer_id else {}
    rating = risk.get("rating")
    reasons = [r["text"] if isinstance(r, dict) else str(r) for r in risk.get("reasons", [])]
    ctx = context_for(user_id, rating, policy, reasons)
    medium, high = ctx.grey_zone
    memory.log_event(store, "tm", f"Monitoring {user_id} (KYC rating {rating or 'none'}; rule set {ctx.rule_set}, "
                     f"medium from {medium}, high from {high})", {"cut_offs": [medium, high],
                                                                   "confidence_threshold": ctx.confidence_threshold})
    if customer_id:
        para = (policy.citations.get("high_risk_review_score") or policy.citations.get("high_risk_confidence_threshold",
                                                                                         "")) if rating == "high" else ""
        memory.record_client(
            store, customer_id, f"TM: monitoring run as {rating or 'unrated'} risk; rule set {ctx.rule_set}, medium "
            f"from {medium}, high from {high}; every medium or high case to a reviewer", "tm", para)
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
                    "kind": "graph", "user_id": tx["user_id"], "remaining": [t["transaction_id"] for t in stream[i + 1:]],
                    "tag": tag, "context": _context_record(ctx)})
                memory.log_event(store, "tm", f"{tx['transaction_id']} referred to a reviewer: band "
                                 f"{question.get('band')}, investigator proposes {question.get('next_step')}", question)
                if customer_id := memory.customer_for_user(tx["user_id"]):
                    memory.record_client(store, customer_id, f"TM: {tx['transaction_id']} referred to a reviewer "
                                         f"(band {question.get('band')})", "tm", "3.5")
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
    customer_id = memory.customer_for_user(user_id)
    if customer_id:
        memory.record_client(store, customer_id, f"TM: reviewer decision recorded on {tx_id}: {answer['decision']}",
                             "tm")
    if resume["kind"] == "hold":
        memory.log_event(store, "tm", f"Reviewer {answer['decision']}d held payment {tx_id}", answer)
        return []
    ctx = _context_from({"user_id": user_id, **resume["context"]})
    agent_for(store).invoke(Command(resume=answer), _thread(user_id, resume["tag"]), context=ctx)
    stream = project()["utils.transaction_processing"].transaction_stream(user_id)
    tx = next(t for t in stream if t["transaction_id"] == tx_id)
    decisions = [_after_decision(store, tx, policy)]
    remaining = [t for t in stream if t["transaction_id"] in resume["remaining"]]
    return decisions + _process(store, policy, remaining, ctx, None, resume["tag"])
