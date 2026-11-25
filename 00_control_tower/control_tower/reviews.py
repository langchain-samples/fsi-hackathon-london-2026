"""One entry point for a human's answer to any parked case, whichever pillar parked it."""
from __future__ import annotations

from langgraph.store.base import BaseStore

from control_tower import memory
from control_tower.pillars import kyc, reg, tm


def answer_review(store: BaseStore, case_id: str, answer: dict) -> dict:
    """`answer` is {"decision": "approve" | "reject" | "block", "notes" or "note": "..."}."""
    case = memory.get_review(store, case_id)
    if case is None or case["status"] != "open":
        raise ValueError(f"No open review case {case_id}")
    if case["pillar"] == "reg":
        return reg.approve_policy(store, case, answer)
    if case["pillar"] == "kyc":
        return kyc.resume_kyc(store, case, answer)
    if case["pillar"] == "tm":
        return {"decisions": tm.resume_tm(store, case, answer, reg.active_policy(store))}
    raise ValueError(f"Unknown pillar {case['pillar']}")
