"""Control Tower portal: the 5-minute demo screen.

    uv run uvicorn portal.app:app --port 8000            # live models, traced to LangSmith
    CONTROL_TOWER_OFFLINE=1 uv run uvicorn portal.app:app --port 8000   # scripted rehearsal, no API key

Four tabs. The Dashboard is the admin's high-level view: counts, and one consolidated queue of every decision
waiting for a human across the pillars, each answered with approve or reject. "Scan" sweeps the pillars for new
work (new applications to screen, onboarded clients' payments to monitor). The KYC, Transaction monitoring and
Regulatory change tabs each show one pillar in full. Work runs in a background thread; the page polls /api/state.

The action buttons call the pillar adapters directly, so the demo does not depend on the supervisor. The
chat box talks to the supervisor (skills over the same store) for questions about a client's lifecycle. The chat
cannot record a human decision: those go through the review inbox only.
"""
from __future__ import annotations

import json
import os
import re
import threading
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from control_tower import memory
from control_tower.loader import REPO_ROOT
from control_tower.pillars import kyc, reg, tm
from control_tower.policy import BASELINE, diff
from control_tower.reviews import answer_review
from portal.kyc_tab import make_router as kyc_tab_router

OFFLINE = os.environ.get("CONTROL_TOWER_OFFLINE") == "1"
if OFFLINE:
    from control_tower import offline
    offline.enable()
    build_supervisor = None   # the chat needs a real model; offline it is switched off, not faked
else:
    from langgraph.checkpoint.memory import InMemorySaver
    from control_tower.supervisor import build_supervisor

FIRM = memory.DEFAULT_FIRM
DOCS_DIR = REPO_ROOT / "03_document_summary_agent/data/documents"
DOCUMENTS = sorted(p.stem for p in DOCS_DIR.glob("*.md"))
DOC_TITLES = {p.stem: p.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip() for p in DOCS_DIR.glob("*.md")}
OFFLINE_DOCUMENTS = ["PS26-7"]   # the only regulation the scripted model has an answer for


def _applicants() -> dict[str, str]:
    """Every applicant in the 01 data, as customer_id -> name. The name is read from the application text only to
    label the register before the client is onboarded; after that the KYC record's name is used."""
    names = {l["customer_id"]: l["name"] for l in memory._links()}
    out = {}
    for line in (REPO_ROOT / "01_kyc_agent/data/applications.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            found = [m for m in re.findall(r"[A-ZÀ-Ý][\w'’-]+(?: [A-ZÀ-Ý][\w'’-]+)+", row["application"].replace("I'm ", ""))
                     if m.split()[0] not in ("New", "Account", "Full", "Date", "My")]
            out[row["customer_id"]] = names.get(row["customer_id"]) or (found[0] if found else "")
    return out


APPLICANTS = _applicants()
SCAN_BATCH = int(os.environ.get("CONTROL_TOWER_SCAN_BATCH", "5"))   # new applications screened per scan


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


REFERENCE = {
    "pep_list": _rows(REPO_ROOT / "01_kyc_agent/data/pep_list.jsonl"),
    "sanctions_list": _rows(REPO_ROOT / "01_kyc_agent/data/sanctions_list.jsonl"),
    "analyst_labels": {r["transaction_id"]: r for r in
                       _rows(REPO_ROOT / "02_transaction_monitoring_agent/data/analyst_labels.jsonl")},
    "firm": next(f for f in _rows(REPO_ROOT / "03_document_summary_agent/data/firms.jsonl") if f["firm_id"] == FIRM),
    "providers": {p["provider_id"]: p for p in
                  _rows(REPO_ROOT / "03_document_summary_agent/data/third_party_providers.jsonl")},
}
_monitored: dict[str, tuple] = {}   # customer_id -> (rating, policy) their payments were last scored under
# Offline, the scripted model only knows the clients with fixtures (data/offline_applicants.jsonl: all of them).
RUNNABLE = offline.covered_customers() & set(APPLICANTS) if OFFLINE else set(APPLICANTS)
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Compliance Control Tower")
_lock = threading.Lock()
_status = {"running": None, "error": None}
_chat = {"log": [], "thread": uuid.uuid4().hex, "agent": None}


def _store():
    return memory.get_store()


def _policy():
    return reg.active_policy(_store(), FIRM)


app.include_router(kyc_tab_router(APPLICANTS, RUNNABLE, lambda: _policy().source_document,   # the KYC tab
                                  run_job=lambda name, fn: _run(name, fn),
                                  screen=lambda cid: kyc.run_kyc(cid, _store(), _policy())))


def _run(name: str, fn) -> None:
    def work():
        try:
            fn()
        except Exception as exc:  # show the failure on the page instead of losing it in the server log
            _status["error"] = f"{name}: {exc}"
            traceback.print_exc()
        finally:
            _status["running"] = None
            _lock.release()

    if not _lock.acquire(blocking=False):
        raise HTTPException(409, f"Still running: {_status['running']}")
    _status.update(running=name, error=None)
    threading.Thread(target=work, daemon=True).start()


def _monitor(customer_id: str) -> None:
    """Score the client's card payments afresh under the settings in force now (rating and live policy)."""
    user_id = memory.user_for_customer(customer_id)
    tm.start_replay(_store(), user_id)
    _monitored[customer_id] = ((memory.get_customer_risk(_store(), customer_id) or {}).get("rating"),
                               _policy().source_document)
    tm.run_tm(user_id, _store(), _policy(), run_tag=uuid.uuid4().hex[:6])


def _refresh(customer_id: str) -> None:
    if any(f["customer_id"] == customer_id for f in memory.open_refresh_flags(_store())):
        kyc.run_kyc(customer_id, _store(), _policy())


def _onboarded(cid: str) -> bool:
    return bool((memory.get_client(_store(), cid) or {}).get("onboarded"))


def _monitor_due(cid: str) -> bool:
    """An onboarded card client whose payments have not been scored under their current rating and live policy."""
    risk = memory.get_customer_risk(_store(), cid) or {}
    return (_onboarded(cid) and bool(memory.user_for_customer(cid)) and cid in RUNNABLE
            and _monitored.get(cid) != (risk.get("rating"), _policy().source_document)
            and not any(r["pillar"] == "tm" for r in memory.reviews_for_customer(_store(), cid)))


def _scan() -> None:
    """Sweep the pillars for new work. Nothing here decides anything: decisions land in the action queue."""
    new = [c for c in APPLICANTS if c in RUNNABLE and memory.get_client(_store(), c) is None]
    # Clients with a card account first, so the ones monitoring can follow up on are screened early.
    new.sort(key=lambda c: memory.user_for_customer(c) is None)
    memory.log_event(_store(), "tower", f"Scan: {len(new)} new application(s) in the queue, screening "
                     f"{min(len(new), SCAN_BATCH)}")
    for cid in new[:SCAN_BATCH]:
        kyc.run_kyc(cid, _store(), _policy())
    for cid in [c for c in APPLICANTS if _monitor_due(c)]:
        _monitor(cid)


def _actions() -> list[dict]:
    """Every decision waiting for a human, from every pillar, in one list. Each is answered approve or reject."""
    store, out = _store(), []
    for r in memory.reviews(store):
        q = r["question"]
        cid = r["case_id"].split(":", 1)[1] if r["pillar"] == "kyc" else memory.customer_for_user(
            r["resume"].get("user_id", "")) if r["pillar"] == "tm" else None
        detail = [*q.get("risk_reasons", []), *q.get("why_review", []),
                  *(f"{c['field']}: {c['before']} -> {c['after']} (para {c['paragraph']})" for c in q.get("changes", []))]
        if q.get("reason"):
            detail.insert(0, f"Agent suggests {q.get('agent_decision')} (score {q.get('risk_score')}, confidence "
                             f"{q.get('confidence')}): {q['reason']}")
        sign_off = q.get("journey") == "sign_off"
        kind = {"kyc": "New account" if cid and not _onboarded(cid) else "Existing client: KYC decision",
                "tm": "Payment review", "reg": "Brief sign-off" if sign_off else "Policy change"}[r["pillar"]]
        subject = APPLICANTS.get(cid, "") if cid else q.get("document_id", "")
        if q.get("transaction_id"):
            subject += f" · {q['transaction_id']}"
        action = {"id": r["case_id"], "pillar": r["pillar"], "kind": kind, "subject": subject, "customer_id": cid,
                  "question": q.get("question", "Decision needed"), "detail": detail,
                  "reject": "block" if r["pillar"] == "tm" else "reject"}
        if r["pillar"] == "reg":  # the 03 reviewer summary and the journey, for the reviewer card
            action.update(journey=q.get("journey", "control_change"), changes=q.get("changes", []),
                          review={k: q[k] for k in ("title", "summary", "review", "key_dates", "counts") if k in q},
                          approve_label="Sign off" if sign_off else "Approve changes",
                          reject_label="Send back" if sign_off else "Reject changes")
        out.append(action)
    for f in memory.open_refresh_flags(store):
        cid = f["customer_id"]
        if not memory.reviews_for_customer(store, cid):
            out.append({"id": f"refresh:{cid}", "pillar": "kyc", "kind": "KYC refresh", "customer_id": cid,
                        "subject": APPLICANTS.get(cid, cid), "question": "Run a KYC refresh for this client?",
                        "detail": [f"Transaction monitoring flagged them: {f['reason']}"], "reject": "reject"})
    live = memory.active_policy_id(store)
    for c in memory.clients(store):
        cid = c["customer_id"]
        if c["assessed_policy"] != live and not memory.reviews_for_customer(store, cid) \
                and not any(a["id"] == f"refresh:{cid}" for a in out):
            changes = ", ".join(f"{d['field']} (para {d['paragraph']})" for d in c["due_changes"][:4])
            out.append({"id": f"reassess:{cid}", "pillar": "kyc", "kind": "Re-assessment", "customer_id": cid,
                        "subject": APPLICANTS.get(cid, cid),
                        "question": f"Re-assess this client under {live}?", "reject": None,
                        "detail": [f"Last assessed under {c['assessed_policy']}; the live policy is {live}.",
                                   *([f"Changed controls include: {changes}"] if changes else [])]})
    return out


def _decide(action_id: str, payload: dict) -> None:
    kind, _, cid = action_id.partition(":")
    if kind == "refresh" and payload["decision"] != "approve":
        memory.close_refresh_flag(_store(), cid)
        memory.record_client(_store(), cid, "KYC refresh dismissed by reviewer" +
                             (f": {payload['notes']}" if payload["notes"] else ""), "kyc")
    elif kind in ("refresh", "reassess"):
        kyc.run_kyc(cid, _store(), _policy())
    else:
        answer_review(_store(), action_id, payload)


ACTIONS = {"onboard": lambda c: kyc.run_kyc(c, _store(), _policy()), "monitor": _monitor, "refresh": _refresh}


def _brief(document_id: str) -> None:
    if OFFLINE:  # the 03 deep agent needs a real model; feed the document text in as the brief instead
        reg.publish_policy_from_brief(document_id, (DOCS_DIR / f"{document_id}.md").read_text(), FIRM, _store())
    else:
        reg.run_reg(document_id, FIRM, _store())


def _ask_supervisor(message: str) -> None:
    """One chat turn. The supervisor's tools use the portal's store, so it sees what the buttons did."""
    if _chat["agent"] is None:
        _chat["agent"] = build_supervisor(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": _chat["thread"]}, "run_name": "control-tower-chat"}
    out = _chat["agent"].invoke({"messages": [{"role": "user", "content": message}]}, config)
    turn = out["messages"][max(i for i, m in enumerate(out["messages"]) if m.type == "human"):]
    calls = [c for m in turn for c in (getattr(m, "tool_calls", None) or [])]
    skills = [c["args"].get("file_path", "").split("/")[-2] for c in calls
              if c["name"] == "read_file" and c["args"].get("file_path", "").endswith("SKILL.md")]
    tools = [c["name"] for c in calls if c["name"] != "read_file"]
    _chat["log"].append({"role": "agent", "text": out["messages"][-1].text, "skills": list(dict.fromkeys(skills)),
                         "tools": tools})


class Question(BaseModel):
    message: str
    customer_id: str = ""


class Answer(BaseModel):
    decision: str
    note: str = ""


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/api/client/{customer_id}/{action}")
def client_action(customer_id: str, action: str):
    if action not in ACTIONS or customer_id not in APPLICANTS:
        raise HTTPException(404, f"No action {action} for {customer_id}")
    if customer_id not in RUNNABLE:
        raise HTTPException(400, "Offline rehearsal only covers the scripted clients. Run live for the others.")
    if action == "monitor":
        user_id = memory.user_for_customer(customer_id)
        if not user_id:
            raise HTTPException(400, f"{customer_id} has no card account, so there is nothing to monitor")
        if memory.get_customer_risk(_store(), customer_id) is None:
            raise HTTPException(400, "Onboard the client first: monitoring thresholds come from the KYC rating")
        if any(r["pillar"] == "tm" for r in memory.reviews_for_customer(_store(), customer_id)):
            raise HTTPException(400, "A payment is waiting in the review inbox. Answer it before monitoring again.")
    _run(f"{action} {customer_id}", lambda: ACTIONS[action](customer_id))
    return {"started": action}


@app.post("/api/regulation/{document_id}")
def regulation(document_id: str):
    if document_id not in (OFFLINE_DOCUMENTS if OFFLINE else DOCUMENTS):
        raise HTTPException(404, f"No document {document_id}" + (" in offline rehearsal" if OFFLINE else ""))
    _run(f"brief {document_id}", lambda: _brief(document_id))
    return {"started": document_id}


@app.post("/api/scan")
def scan():
    _run("scan", _scan)
    return {"started": "scan"}


@app.post("/api/action/{action_id}")
def decide(action_id: str, answer: Answer):
    """The one door for a human decision, whichever pillar raised it."""
    if not any(a["id"] == action_id for a in _actions()):
        raise HTTPException(404, f"No open action {action_id}")
    payload = {"decision": answer.decision, "notes": answer.note, "note": answer.note}
    _run(f"decision on {action_id}", lambda: _decide(action_id, payload))
    return {"started": action_id}


@app.get("/api/reference")
def reference():
    firm = REFERENCE["firm"]
    return {"pep_list": REFERENCE["pep_list"], "sanctions_list": REFERENCE["sanctions_list"],
            "tm_rule_sets": tm.rule_sets(),
            "firm": {**firm, "providers": [{**REFERENCE["providers"].get(p["provider_id"], {}), **p}
                                           for p in firm.get("providers", [])]}}


@app.get("/api/bank")
def bank():
    """Everything recorded about the bank in one read-only view: profile, senior managers, important business
    services, third parties and contracts, clients, controls in force and the regulation library."""
    store = _store()
    facts_tool = reg.load_project(reg.FOLDER)["tools.firm_facts"]
    context = facts_tool.bank_context(FIRM)
    size = facts_tool.get_firm_facts(DOCUMENTS[0], FIRM)["small_firm_test"] if DOCUMENTS else {}
    raw = REFERENCE["firm"]
    contracts = [{**REFERENCE["providers"].get(p["provider_id"], {}), **p,
                  "supports": [s["name"] for s in context["business_services"]
                               if REFERENCE["providers"].get(p["provider_id"], {}).get("name") in s["providers"]]}
                 for p in raw.get("providers", [])]

    records = memory.clients(store)
    ratings = {}
    for c in records:
        risk = memory.get_customer_risk(store, c["customer_id"]) or {}
        ratings[risk.get("rating") or "not rated"] = ratings.get(risk.get("rating") or "not rated", 0) + 1
    stages = {}
    for c in records:
        stages[c["stage"]] = stages.get(c["stage"], 0) + 1

    live = reg.active_policy(store, FIRM)
    controls = [{"field": f, "baseline": getattr(BASELINE, f), "live": getattr(live, f),
                 "paragraph": live.citations.get(f, "")}
                for f in type(live).model_fields if f not in ("citations", "source_document")]

    library = []
    all_reviews = memory.reviews(store, None)
    for doc in DOCUMENTS:
        cases = [r for r in all_reviews if r["pillar"] == "reg" and r["question"].get("document_id") == doc]
        case = cases[-1] if cases else None
        if live.source_document == doc:
            status = "Live policy"
        elif case is None:
            status = "Not briefed"
        elif case["status"] == "open":
            status = "Awaiting sign-off" if case["question"].get("journey") == "sign_off" else "Awaiting approval"
        else:
            decision = (case.get("answer") or {}).get("decision")
            signed = case["question"].get("journey") == "sign_off"
            status = ("Signed off" if signed else "Approved") if decision == "approve" else \
                     ("Sent back" if signed else "Rejected")
        review = (case or {}).get("question", {}).get("review") or {}
        library.append({"document_id": doc, "status": status, "title": DOC_TITLES.get(doc, doc),
                        "journey": (case or {}).get("question", {}).get("journey"),
                        "classification": review.get("classification"), "impact": review.get("impact"),
                        "effective_date": review.get("effective_date")})

    return {"firm": {k: raw.get(k) for k in ("firm_id", "name", "type", "customers", "total_assets", "notes")},
            "small_firm_test": size, "senior_managers": context["senior_managers"],
            "business_services": context["business_services"], "shared_providers": context["shared_providers"],
            "gaps": context["gaps"], "contracts": contracts,
            "clients": {"applicants": len(APPLICANTS), "on_file": len(records), "by_rating": ratings,
                        "by_stage": stages},
            "policy": live.source_document, "controls": controls, "library": library}


@app.post("/api/chat")
def chat(question: Question):
    if OFFLINE:
        raise HTTPException(400, "The chat needs live models. It is switched off in offline rehearsal.")
    if not question.message.strip():
        raise HTTPException(400, "Empty message")

    def work():
        try:
            who = question.customer_id
            context = f"[Client selected in the portal: {who} {APPLICANTS.get(who, '')}]\n" if who in APPLICANTS else ""
            _ask_supervisor(context + question.message)
        except Exception as exc:
            _chat["log"].append({"role": "error", "text": f"The supervisor failed: {exc}", "skills": [], "tools": []})
            raise

    _run("supervisor chat", work)
    _chat["log"].append({"role": "user", "text": question.message, "skills": [], "tools": []})
    return {"started": True}


@app.post("/api/reset")
def reset():
    if _status["running"]:
        raise HTTPException(409, "A step is still running")
    memory.reset_store()
    tm._agents.clear()
    _monitored.clear()
    _chat.update(log=[], thread=uuid.uuid4().hex)
    return {"reset": True}


def _expected_band(label: dict | None) -> str | None:
    """The band the analyst label implies: blocks high, clear approvals low, ambiguous cases medium."""
    if not label:
        return None
    return "high" if label["decision"] == "block" else "medium" if label.get("ambiguous") else "low"


def _tm_metrics(transactions: list[dict]) -> dict:
    """The TM controls, measured on this client's payments. Review routing is the safety metric."""
    decided = [t for t in transactions if t["decision"]]
    needs_human = [t for t in transactions if (t["decision"] or {}).get("band") in ("medium", "high")
                   or (t["decision"] or {}).get("decided_by") == "human" or t["review"]]
    reached = [t for t in needs_human if t["review"] or (t["decision"] or {}).get("decided_by") == "human"]
    labelled = [t for t in transactions if t["expected_band"]]
    sampled = [t for t in decided if t["decision"].get("sample")]
    return {
        "decided": len(decided), "by_rules": sum(1 for t in decided if t["decision"]["decided_by"] == "rules"),
        "models_ran": sum(1 for t in decided if t["decision"].get("investigations")),
        "needs_human": len(needs_human), "reached_human": len(reached),
        "labelled": len(labelled), "band_agrees": sum(1 for t in labelled if t["band"] == t["expected_band"]),
        "sampled": len(sampled),
        "sample_escalated": sum(1 for t in sampled if (t["decision"].get("report") or {}).get("next_step")
                                not in (None, "close_explained")),
        "audit_returns": sum(t["decision"].get("audit_returns") or 0 for t in decided),
        "facts_failed": sum(len((t["decision"].get("audit_detail") or {}).get("facts_failed", [])) for t in decided),
    }


def _detail(cid: str) -> dict:
    store, policy = _store(), _policy()
    user_id = memory.user_for_customer(cid)
    risk, client = memory.get_customer_risk(store, cid), memory.get_client(store, cid)
    out = {"customer_id": cid, "user_id": user_id, "name": (risk or {}).get("name") or APPLICANTS[cid], "risk": risk,
           "client": {**client, "stage": memory.client_stage(store, cid)} if client else None,
           "onboarded": bool((client or {}).get("onboarded")), "monitor_due": _monitor_due(cid),
           "runnable": cid in RUNNABLE, "thresholds": None, "transactions": [], "alerts": [],
           "refresh": any(f["customer_id"] == cid for f in memory.open_refresh_flags(store)),
           "tm_waiting": any(r["pillar"] == "tm" for r in memory.reviews_for_customer(store, cid))}
    if user_id:
        decisions = {i.key: i.value for i in store.search(("decisions", user_id), limit=100)}
        reasons = [r["text"] if isinstance(r, dict) else str(r) for r in (risk or {}).get("reasons", [])]
        ctx = tm.context_for(user_id, (risk or {}).get("rating"), policy, reasons)
        preview = tm.triage_preview(user_id, ctx)
        reviews = {r["case_id"].split(":", 1)[1]: r for r in memory.reviews(store)
                   if r["pillar"] == "tm" and r["resume"].get("user_id") == user_id}
        transactions = []
        for t in tm.project()["utils.transaction_processing"].transaction_stream(user_id):
            tx_id, decision, review = t["transaction_id"], decisions.get(t["transaction_id"]), reviews.get(t["transaction_id"])
            label = REFERENCE["analyst_labels"].get(tx_id)
            # The band the payment was decided in; before monitoring runs, what the rules say under today's settings.
            band = (decision or {}).get("band") or (review or {}).get("question", {}).get("band") or preview[tx_id]["band"]
            transactions.append({**t, "decision": decision, "analyst": label, "expected_band": _expected_band(label),
                                 "triage": preview[tx_id], "band": band,
                                 "review": {"id": review["case_id"], "question": review["question"]} if review else None})
        flag = next((f for f in memory.open_refresh_flags(store) if f["customer_id"] == cid), None)
        out.update(thresholds={"confidence": ctx.confidence_threshold, "grey_zone": list(ctx.grey_zone)},
                   tm={"rating": ctx.kyc_rating, "rule_set": ctx.rule_set, "cut_offs": list(ctx.grey_zone),
                       "client_band": "high_risk" if ctx.kyc_rating == "high" else "standard",
                       "confidence_threshold": ctx.confidence_threshold, "policy": ctx.policy_version,
                       "cut_off_paragraph": policy.citations.get("high_risk_review_score", "")
                       if ctx.kyc_rating == "high" else ""},
                   transactions=transactions, alerts=memory.alerts_for(store, user_id),
                   conduct=tm.conduct_score(store, user_id), refresh_flag=flag,
                   tm_summary=memory.get_tm_summary(store, cid), metrics=_tm_metrics(transactions))
    return out


@app.get("/api/state")
def state(after: int = 0, customer: str = ""):
    store, policy = _store(), _policy()
    register = []
    for cid, name in APPLICANTS.items():
        risk, client = memory.get_customer_risk(store, cid) or {}, memory.get_client(store, cid)
        register.append({
            "customer_id": cid, "name": risk.get("name") or name, "rating": risk.get("rating"),
            "stage": memory.client_stage(store, cid) if client else "not_screened",
            "status": risk.get("status") or "not_screened", "onboarded": bool((client or {}).get("onboarded")),
            "policy": risk.get("policy"), "reasons": risk.get("reasons", []),
            "has_card": bool(memory.user_for_customer(cid)), "runnable": cid in RUNNABLE})
    actions = _actions()
    briefs = {}  # the latest brief for each document, so every briefed regulation keeps its own summary
    for e in memory.events(store):
        if e["title"].startswith("Brief ready for "):
            doc = e["title"].removeprefix("Brief ready for ")
            briefs[doc] = {"document": doc, "title": DOC_TITLES.get(doc, doc), "text": e["detail"]["brief"]}
    brief = briefs[doc] if briefs else None  # the most recent one, as before
    live = policy.source_document
    return {
        "offline": OFFLINE, "status": _status, "policy": policy.model_dump(),
        "policy_changes": diff(BASELINE, policy), "clients": register, "actions": actions,
        "kpis": {
            "new_applications": sum(1 for c in register if c["stage"] == "not_screened"),
            "awaiting_decision": len(actions),
            "onboarded": sum(1 for c in register if c["onboarded"]),
            "rejected": sum(1 for c in register if c["status"] == "rejected"),
            "refresh_or_reassess": sum(1 for a in actions if a["kind"] in ("KYC refresh", "Re-assessment")),
            "payments_for_review": sum(1 for a in actions if a["pillar"] == "tm"),
            "monitoring_due": sum(1 for c in register if _monitor_due(c["customer_id"])),
        },
        "customer": _detail(customer) if customer in APPLICANTS else None,
        "card_clients": [_detail(c["customer_id"]) for c in register if c["has_card"]],
        "documents": OFFLINE_DOCUMENTS if OFFLINE else DOCUMENTS,
        "document_titles": DOC_TITLES,
        "brief": brief,
        "briefs": briefs,
        "affected": [{"customer_id": c["customer_id"], "name": c["name"], "policy": c["policy"], "rating": c["rating"]}
                     for c in register if c["policy"] and c["policy"] != live],
        "events": memory.events(store, after), "langsmith_project": os.environ.get("LANGSMITH_PROJECT", ""),
        "chat": _chat["log"],
    }
