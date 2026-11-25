"""The shared memory every pillar reads and writes: one LangGraph Store, a few namespaces.

The pillars never call each other. They meet here, so each one can still run on its own:

| Namespace                       | Key            | Written by          | Read by              |
|---------------------------------|----------------|---------------------|----------------------|
| ("policy", firm_id)             | "active"       | regulatory pillar   | KYC, TM              |
| ("customer_risk",)              | customer_id    | KYC pillar          | TM, supervisor       |
| ("alerts", user_id)             | transaction_id | TM pillar           | refresh check        |
| ("kyc_refresh",)                | customer_id    | TM pillar           | supervisor -> KYC    |
| ("reviews",)                    | case_id        | any pillar          | portal / reviewer    |
| ("events",)                     | sequence no.   | everything          | portal timeline      |
| ("client",)                     | customer_id    | the adapters (code) | supervisor, portal   |
| ("tm_summary",)                 | customer_id    | TM pillar           | KYC refresh, portal  |

The client file is the control tower's memory of one client across the whole lifecycle: the stage they are in, the
policy their last assessment was made under, and a dated history of what happened and why. Only code writes it
(`record_client`, called by the adapters); the supervisor reads it and never edits it.

The TM project also keeps its own ("decisions", user_id) and ("customer_notes", user_id) namespaces in the same
store, so the supervisor can read those too.
"""
from __future__ import annotations

import itertools
import json
import time
from pathlib import Path

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_FIRM = "FIRM-001"

_store: BaseStore | None = None
_seq = itertools.count(1)


def get_store() -> BaseStore:
    """The process-wide store used by the portal and scripts. Under `langgraph dev`, tools get the server's
    store from `runtime.store` instead, and pass it down explicitly."""
    global _store
    if _store is None:
        _store = InMemoryStore()
    return _store


def reset_store() -> BaseStore:
    global _store, _seq
    _store, _seq = InMemoryStore(), itertools.count(1)
    return _store


# --- customer identity across pillars ---------------------------------------------------------------------

def _links() -> list[dict]:
    with open(DATA_DIR / "customer_links.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def user_for_customer(customer_id: str) -> str | None:
    """KYC knows customers as CUST-xxx and TM as USR-xxx; this maps one to the other."""
    return next((l["user_id"] for l in _links() if l["customer_id"] == customer_id), None)


def customer_for_user(user_id: str) -> str | None:
    return next((l["customer_id"] for l in _links() if l["user_id"] == user_id), None)


# --- events (the portal's timeline) ---------------------------------------------------------------------

def log_event(store: BaseStore, pillar: str, title: str, detail: dict | None = None) -> None:
    seq = next(_seq)
    store.put(("events",), f"{seq:06d}", {"seq": seq, "ts": time.time(), "pillar": pillar, "title": title,
                                         "detail": detail or {}})


def events(store: BaseStore, after: int = 0) -> list[dict]:
    items = [i.value for i in store.search(("events",), limit=10_000)]
    return sorted((e for e in items if e["seq"] > after), key=lambda e: e["seq"])


# --- policy ---------------------------------------------------------------------------------------------

def put_policy(store: BaseStore, policy: dict, firm_id: str = DEFAULT_FIRM) -> None:
    store.put(("policy", firm_id), "active", policy)


def get_policy_dict(store: BaseStore, firm_id: str = DEFAULT_FIRM) -> dict | None:
    item = store.get(("policy", firm_id), "active")
    return item.value if item else None


# --- customer risk --------------------------------------------------------------------------------------

def put_customer_risk(store: BaseStore, customer_id: str, rating: dict) -> None:
    store.put(("customer_risk",), customer_id, rating)


def get_customer_risk(store: BaseStore, customer_id: str) -> dict | None:
    item = store.get(("customer_risk",), customer_id)
    return item.value if item else None


# --- alerts and KYC refresh -----------------------------------------------------------------------------

def put_alert(store: BaseStore, user_id: str, alert: dict) -> None:
    store.put(("alerts", user_id), alert["transaction_id"], alert)


def alerts_for(store: BaseStore, user_id: str) -> list[dict]:
    return sorted((i.value for i in store.search(("alerts", user_id), limit=1000)), key=lambda a: a["timestamp"])


def flag_kyc_refresh(store: BaseStore, customer_id: str, reason: str, **extra) -> None:
    """`extra` carries optional fields such as TM's `due_by` (10 business days after the trigger, para 5.5)."""
    store.put(("kyc_refresh",), customer_id, {"customer_id": customer_id, "reason": reason, "open": True, **extra})


def open_refresh_flags(store: BaseStore) -> list[dict]:
    return [i.value for i in store.search(("kyc_refresh",), limit=1000) if i.value.get("open")]


def close_refresh_flag(store: BaseStore, customer_id: str) -> None:
    item = store.get(("kyc_refresh",), customer_id)
    if item:
        store.put(("kyc_refresh",), customer_id, {**item.value, "open": False})


# --- TM conduct score: green, amber or red for the period since the last review ------------------------------

def put_tm_summary(store: BaseStore, customer_id: str, summary: dict) -> None:
    """Written by the TM adapter when a KYC refresh opens, so the refresh reviewer sees how the client behaved."""
    store.put(("tm_summary",), customer_id, summary)


def get_tm_summary(store: BaseStore, customer_id: str) -> dict | None:
    item = store.get(("tm_summary",), customer_id)
    return item.value if item else None


# --- human review cases ---------------------------------------------------------------------------------

def open_review(store: BaseStore, case_id: str, pillar: str, question: dict, resume: dict) -> None:
    """Park a decision for a human. `resume` says how to continue the paused pillar (thread id etc.)."""
    store.put(("reviews",), case_id, {"case_id": case_id, "pillar": pillar, "question": question,
                                      "resume": resume, "status": "open", "answer": None})


def reviews(store: BaseStore, status: str | None = "open") -> list[dict]:
    items = [i.value for i in store.search(("reviews",), limit=1000)]
    return [r for r in items if status is None or r["status"] == status]


def get_review(store: BaseStore, case_id: str) -> dict | None:
    item = store.get(("reviews",), case_id)
    return item.value if item else None


def close_review(store: BaseStore, case_id: str, answer: dict) -> None:
    case = get_review(store, case_id)
    if case:
        store.put(("reviews",), case_id, {**case, "status": "closed", "answer": answer})


# --- the client file: one record per client across the whole lifecycle -----------------------------------

STAGES = ("onboarding", "active", "reassessment_due", "under_review", "refresh_due")


def active_policy_id(store: BaseStore, firm_id: str = DEFAULT_FIRM) -> str:
    return (get_policy_dict(store, firm_id) or {}).get("source_document", "baseline")


def reviews_for_customer(store: BaseStore, customer_id: str) -> list[dict]:
    """Open cases about this client: KYC cases carry the customer ID, TM cases the linked card user."""
    user_id = user_for_customer(customer_id)
    return [r for r in reviews(store)
            if customer_id in r["case_id"] or (user_id and r["resume"].get("user_id") == user_id)]


def client_stage(store: BaseStore, customer_id: str, firm_id: str = DEFAULT_FIRM) -> str:
    """Where the client is in the lifecycle, worked out from the facts in the store so it cannot drift from them."""
    risk = get_customer_risk(store, customer_id)
    if reviews_for_customer(store, customer_id):
        return "under_review"
    if risk is None:
        return "onboarding"
    if any(f["customer_id"] == customer_id for f in open_refresh_flags(store)):
        return "refresh_due"
    if risk.get("policy") != active_policy_id(store, firm_id):
        return "reassessment_due"
    return "active"


def get_client(store: BaseStore, customer_id: str) -> dict | None:
    item = store.get(("client",), customer_id)
    return item.value if item else None


def clients(store: BaseStore) -> list[dict]:
    return sorted((i.value for i in store.search(("client",), limit=1000)), key=lambda c: c["customer_id"])


def record_client(store: BaseStore, customer_id: str, what: str | None = None, pillar: str = "",
                  paragraph: str = "", **fields) -> dict:
    """Update a client file: set `fields`, add a history line if `what` is given, and refresh the stage.

    A stage change is written to the history too, so the file reads as the client's lifecycle from top to bottom.
    """
    client = get_client(store, customer_id) or {
        "customer_id": customer_id, "user_id": user_for_customer(customer_id), "stage": "onboarding",
        "assessed_policy": None, "due_changes": [], "history": []}
    client.update(fields)
    history = list(client["history"])
    policy = active_policy_id(store)
    if what:
        history.append({"ts": time.time(), "pillar": pillar, "what": what, "paragraph": paragraph,
                        "policy": policy})
    stage = client_stage(store, customer_id)
    if stage != client["stage"]:
        history.append({"ts": time.time(), "pillar": "lifecycle", "paragraph": "", "policy": policy,
                        "what": f"Stage: {client['stage']} -> {stage}"})
    client.update(stage=stage, history=history)
    store.put(("client",), customer_id, client)
    return client


def mark_policy_change(store: BaseStore, document_id: str, changes: list[dict]) -> list[str]:
    """A new policy is live: every client last assessed under another policy is now due a re-assessment.

    Nothing is re-run here. The client is only marked, with the changed controls, so a human can choose who to
    re-assess. Returns the customer IDs that were marked.
    """
    due = [{"field": c["field"], "paragraph": c.get("paragraph", "")} for c in changes]
    paragraphs = ", ".join(dict.fromkeys(c["paragraph"] for c in due if c["paragraph"]))
    marked = []
    for client in clients(store):
        if client["assessed_policy"] and client["assessed_policy"] != document_id:
            record_client(store, client["customer_id"], pillar="reg", paragraph=paragraphs, due_changes=due,
                          what=f"{document_id} approved ({len(due)} control changes): re-assessment due; last "
                               f"assessed under {client['assessed_policy']}")
            marked.append(client["customer_id"])
    return marked
