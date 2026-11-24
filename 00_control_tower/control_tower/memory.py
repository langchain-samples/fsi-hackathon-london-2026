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


def flag_kyc_refresh(store: BaseStore, customer_id: str, reason: str) -> None:
    store.put(("kyc_refresh",), customer_id, {"customer_id": customer_id, "reason": reason, "open": True})


def open_refresh_flags(store: BaseStore) -> list[dict]:
    return [i.value for i in store.search(("kyc_refresh",), limit=1000) if i.value.get("open")]


def close_refresh_flag(store: BaseStore, customer_id: str) -> None:
    item = store.get(("kyc_refresh",), customer_id)
    if item:
        store.put(("kyc_refresh",), customer_id, {**item.value, "open": False})


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
