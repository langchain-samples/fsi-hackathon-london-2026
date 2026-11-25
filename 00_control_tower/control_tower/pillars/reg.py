"""Regulatory pillar: runs Alejandro's project 03 agent and turns its brief into policy for KYC and TM.

The adapter only needs the 03 agent's final result (the brief). Since 03 returns a `ReviewedBrief`, the brief also
carries a reviewer summary (sections A to E), which the review case shows to the person deciding.

Two journeys, decided in code:

- **Control change.** The brief implies changes to the KYC or TM controls (PS26-7). They are parked as a policy case
  and go live only when a reviewer approves them (`approve_policy`), unless `auto_approve=True` (evals, scripts).
- **Sign-off only.** The brief changes no control (PS26-4, operational resilience). It is parked as a brief case for
  a reviewer to sign off or send back. Nothing changes in KYC or TM either way.

A document changes only the controls it cites, on top of the live policy, and never resets a control to its default
(`merge_policy`). So briefing a second regulation cannot silently undo the first.
"""
from __future__ import annotations

import json

from langgraph.store.base import BaseStore

from control_tower import memory
from control_tower.contracts import brief_from_result, reg_request
from control_tower.loader import load_project
from control_tower.policy import BASELINE, PolicyParams, diff, extract_policy
from control_tower.settings import MODEL

FOLDER = "03_document_summary_agent"
_agent = None


def reg_agent():
    """The 03 agent: a deep agent with parallel section-summariser sub-agents, then the reviewer summary."""
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


def merge_policy(active: PolicyParams, extracted: PolicyParams) -> PolicyParams:
    """The live policy with only the controls the document cites, set to the document's values.

    A control the document does not cite keeps its live value, and a cited control is never set back to its default:
    a regulation that says nothing about a control must not switch it off. Citations are kept for every cited control
    whose value the document confirms, including one that does not change.
    """
    updates, citations = {}, dict(active.citations)
    for name in PolicyParams.model_fields:
        if name in ("citations", "source_document") or name not in extracted.citations:
            continue
        value = getattr(extracted, name)
        if value != getattr(BASELINE, name):
            updates[name] = value
        if value == updates.get(name, getattr(active, name)):
            citations[name] = extracted.citations[name]
    return active.model_copy(update={**updates, "citations": citations,
                                     "source_document": extracted.source_document})


def reviewer_summary(brief: str) -> dict:
    """The 03 reviewer summary (A to E), key dates and counts from the brief, or {} for a plain-text brief."""
    try:
        data = json.loads(brief)
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: data[k] for k in ("title", "summary", "review", "key_dates", "counts") if k in data}


def run_reg(document_id: str, firm_id: str, store: BaseStore, model: str = MODEL, auto_approve: bool = False,
            extractor=extract_policy) -> dict:
    """Brief the document for the firm with the 03 agent, then park it for the right human decision."""
    memory.log_event(store, "reg", f"Briefing {document_id} for {firm_id}")
    result = reg_agent().invoke(reg_request(document_id, firm_id))
    brief = brief_from_result(result)
    memory.log_event(store, "reg", f"Brief ready for {document_id}", {"brief": brief})
    return publish_policy_from_brief(document_id, brief, firm_id, store, model, auto_approve, extractor)


def publish_policy_from_brief(document_id: str, brief: str, firm_id: str, store: BaseStore, model: str = MODEL,
                              auto_approve: bool = False, extractor=extract_policy) -> dict:
    active = active_policy(store, firm_id)
    proposed = merge_policy(active, extractor(brief, document_id, model, source_text=document_text(document_id)))
    changes = diff(active, proposed)
    summary = reviewer_summary(brief)

    if not changes:
        memory.log_event(store, "reg", f"{document_id} changes no KYC or TM control: brief parked for sign-off")
        if auto_approve:
            return {"status": "no_change", "journey": "sign_off", "changes": [], "policy": active.model_dump()}
        case_id = f"brief:{firm_id}:{document_id}"
        memory.open_review(store, case_id, "reg", {
            "question": f"Sign off the {document_id} brief for {firm_id}? It changes no KYC or monitoring control.",
            "changes": [], "document_id": document_id, "journey": "sign_off", **summary,
        }, {"kind": "brief", "firm_id": firm_id, "document_id": document_id})
        return {"status": "pending_review", "journey": "sign_off", "case_id": case_id, "changes": []}

    memory.log_event(store, "reg", f"{document_id} proposes {len(changes)} control changes", {"changes": changes})
    if auto_approve:
        _activate(store, firm_id, proposed)
        return {"status": "active", "journey": "control_change", "changes": changes, "policy": proposed.model_dump()}
    case_id = f"policy:{firm_id}:{document_id}"
    memory.open_review(store, case_id, "reg", {
        "question": f"Approve the control changes from {document_id} for {firm_id}?",
        "changes": changes, "document_id": document_id, "journey": "control_change", **summary,
    }, {"kind": "policy", "firm_id": firm_id, "proposed": proposed.model_dump()})
    return {"status": "pending_review", "journey": "control_change", "case_id": case_id, "changes": changes}


def approve_policy(store: BaseStore, case: dict, answer: dict) -> dict:
    """A reviewer's answer to a regulatory case: approve or reject control changes, or sign off or return a brief."""
    memory.close_review(store, case["case_id"], answer)
    note = answer.get("notes") or answer.get("note") or ""
    if case["resume"].get("kind") == "brief":
        document_id = case["resume"]["document_id"]
        if answer.get("decision") == "approve":
            memory.log_event(store, "reg", f"{document_id} brief signed off", answer)
            return {"status": "signed_off", "document_id": document_id, "note": note}
        memory.log_event(store, "reg", f"{document_id} brief sent back for another look", answer)
        return {"status": "returned", "document_id": document_id, "note": note}
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
