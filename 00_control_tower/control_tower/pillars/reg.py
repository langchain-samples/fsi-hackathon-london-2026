"""Regulatory pillar: runs Alejandro's project 03 deep agent and turns its brief into policy for KYC and TM.

The adapter only needs the 03 agent's final message (the brief). Alejandro can reshape it freely (Version B, the
Sections A-E format, extra firms): `publish_policy_from_brief` reads whatever text comes back.

A policy change never goes live silently. It is parked as a review case and becomes active only when a reviewer
approves it (`approve_policy`), unless `auto_approve=True` (used in evals and scripted runs).
"""
from __future__ import annotations

from langgraph.store.base import BaseStore

from control_tower import memory
from control_tower.contracts import brief_from_result, reg_request
from control_tower.loader import load_project
from control_tower.policy import BASELINE, PolicyParams, diff, extract_policy
from control_tower.settings import MODEL

FOLDER = "03_document_summary_agent"
_agent = None


def reg_agent():
    """The 03 deep agent (main agent plus parallel section-summariser sub-agents)."""
    global _agent
    if _agent is None:
        _agent = load_project(FOLDER)["agent"].build_agent()
    return _agent


def document_text(document_id: str) -> str:
    """The regulation's own text from the 03 library, with its paragraph numbers; "" if it is not in the library."""
    try:
        document = load_project(FOLDER)["tools.documents"].get_document(document_id)
    except ValueError:
        return ""
    return "\n\n".join(f"## {s['number']}. {s['title']}\n\n{s['text']}" for s in document["sections"])


def active_policy(store: BaseStore, firm_id: str = memory.DEFAULT_FIRM) -> PolicyParams:
    raw = memory.get_policy_dict(store, firm_id)
    return PolicyParams(**raw) if raw else BASELINE


def run_reg(document_id: str, firm_id: str, store: BaseStore, model: str = MODEL, auto_approve: bool = False,
            extractor=extract_policy) -> dict:
    """Brief the document for the firm with the 03 agent, then propose the policy it implies."""
    memory.log_event(store, "reg", f"Briefing {document_id} for {firm_id}")
    result = reg_agent().invoke(reg_request(document_id, firm_id))
    brief = brief_from_result(result)
    memory.log_event(store, "reg", f"Brief ready for {document_id}", {"brief": brief})
    return publish_policy_from_brief(document_id, brief, firm_id, store, model, auto_approve, extractor)


def publish_policy_from_brief(document_id: str, brief: str, firm_id: str, store: BaseStore, model: str = MODEL,
                              auto_approve: bool = False, extractor=extract_policy) -> dict:
    proposed = extractor(brief, document_id, model, source_text=document_text(document_id))
    changes = diff(active_policy(store, firm_id), proposed)
    memory.log_event(store, "reg", f"{document_id} proposes {len(changes)} control changes", {"changes": changes})
    case_id = f"policy:{firm_id}:{document_id}"
    if auto_approve:
        _activate(store, firm_id, proposed)
        return {"status": "active", "changes": changes, "policy": proposed.model_dump()}
    memory.open_review(store, case_id, "reg", {
        "question": f"Approve the control changes from {document_id} for {firm_id}?",
        "changes": changes, "document_id": document_id,
    }, {"kind": "policy", "firm_id": firm_id, "proposed": proposed.model_dump()})
    return {"status": "pending_review", "case_id": case_id, "changes": changes}


def approve_policy(store: BaseStore, case: dict, answer: dict) -> dict:
    memory.close_review(store, case["case_id"], answer)
    if answer.get("decision") != "approve":
        memory.log_event(store, "reg", "Policy change rejected", answer)
        return {"status": "rejected"}
    proposed = PolicyParams(**case["resume"]["proposed"])
    _activate(store, case["resume"]["firm_id"], proposed)
    return {"status": "active", "policy": proposed.model_dump()}


def _activate(store: BaseStore, firm_id: str, policy: PolicyParams) -> None:
    changes = diff(active_policy(store, firm_id), policy)
    memory.put_policy(store, policy.model_dump(), firm_id)
    marked = memory.mark_policy_change(store, policy.source_document, changes)
    memory.log_event(store, "reg", f"Policy {policy.source_document} is live for {firm_id}",
                     {"policy": policy.model_dump()})
    memory.log_event(store, "reg", f"{len(marked)} client(s) due a re-assessment under {policy.source_document}",
                     {"customers": marked})
