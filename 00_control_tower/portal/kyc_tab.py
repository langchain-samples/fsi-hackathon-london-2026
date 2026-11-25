"""KYC tab: the KYC pillar laid out on the KYC process map.

    GET /api/kyc/pipeline           every applicant, placed on the map, with counts per stage
    GET /api/kyc/case/{customer}    one case file: each step of the map with its status and evidence
    POST /api/kyc/screen-new        screen every new application (one background job, one by one)
    POST /api/kyc/reassess-due      re-assess every client due under the live policy
    GET /kyc/kyc.js, /kyc/kyc.css   the tab's front end (kept out of index.html to avoid merge clashes)

The map (board "KYC Onboarding Agent – LangGraph Process Map", docs/process_maps/01_kyc.md):

    Onboarding:  START -> 3 Profile -> 5 Screening -> 6 Risk assessment -> 7 Approval -> END (onboarded / rejected)
    Monitoring:  TRIGGER (policy change, or transaction alerts) -> R1 Re-assessment -> R2 Refresh -> START again

Steps 1, 2, 3b and 4 of the map (document checklist, document collection, analyst review of the extraction) are
for document-based onboarding. A retail application here is one form, so they are shown as not used.

The two bulk actions only run screening, which a person starts by pressing the button; anything that needs a human
decision lands in the action queue as usual. Decisions still go through POST /api/action/{id}, the portal's one
door for a human decision.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from control_tower import memory
from control_tower.pillars import kyc

STATIC = Path(__file__).parent / "static"

# Lanes and stages, in map order. `count` stages hold customers; the others are steps a case passes through.
STAGES = [
    {"id": "new", "lane": "onboarding", "map": "START", "label": "New application", "count": True},
    {"id": "profile", "lane": "onboarding", "map": "3", "label": "Profile extracted", "count": False},
    {"id": "screening", "lane": "onboarding", "map": "5", "label": "Screening", "count": False},
    {"id": "risk", "lane": "onboarding", "map": "6", "label": "Risk assessment", "count": False},
    {"id": "approval", "lane": "onboarding", "map": "7", "label": "Waiting for a decision", "count": True},
    {"id": "onboarded", "lane": "onboarding", "map": "END", "label": "Onboarded", "count": True},
    {"id": "rejected", "lane": "onboarding", "map": "END", "label": "Rejected", "count": True},
    {"id": "trigger", "lane": "monitoring", "map": "TRIGGER", "label": "Policy change or alerts", "count": False},
    {"id": "reassessment_due", "lane": "monitoring", "map": "R1", "label": "Re-assessment due", "count": True},
    {"id": "refresh_due", "lane": "monitoring", "map": "R2", "label": "Refresh due", "count": True},
]
NOT_USED = [
    {"map": "1", "title": "Draft checklist"},
    {"map": "2", "title": "Collect documents"},
    {"map": "3b", "title": "Analyst review of the extraction"},
    {"map": "4", "title": "Update checklist"},
]
NEXT_ACTION = {
    "new": "Screen the application",
    "approval": "Approve or reject",
    "onboarded": "None: active client",
    "rejected": "None",
    "reassessment_due": "Re-assess under the live policy",
    "refresh_due": "Decide on a KYC refresh",
}


def _kyc_review(store, customer_id: str) -> dict | None:
    return next((r for r in memory.reviews(store) if r["pillar"] == "kyc" and r["case_id"] == f"kyc:{customer_id}"),
                None)


def stage_of(store, customer_id: str, live_policy: str) -> str:
    """Where the customer sits on the map, from the facts in the store."""
    risk = memory.get_customer_risk(store, customer_id)
    if risk is None:
        return "new"
    if _kyc_review(store, customer_id) or risk.get("status") == "pending_review":
        return "approval"
    if any(f["customer_id"] == customer_id for f in memory.open_refresh_flags(store)):
        return "refresh_due"
    if risk.get("status") == "rejected":
        return "rejected"
    if risk.get("policy") != live_policy:
        return "reassessment_due"
    return "onboarded"


def _graph_values(store, risk: dict | None) -> dict:
    """The 01 graph's state for the customer's latest run (application, profile, hits, memo)."""
    if not risk or not risk.get("kyc_thread"):
        return {}
    try:
        return dict(kyc.graph(store).get_state({"configurable": {"thread_id": risk["kyc_thread"]}}).values)
    except Exception:  # the checkpoint is gone (for example after a reset): show what the store has
        return {}


def _approval(risk: dict | None, review: dict | None, values: dict, last: dict | None = None) -> dict:
    if not risk:
        return {"route": None, "outcome": None}
    if review:
        edd = review["resume"].get("kind") == "edd"
        return {"route": "EDD sign-off" if edd else "Human review (high risk)", "outcome": "waiting",
                "case_id": review["case_id"],
                "question": review["question"].get("question", "Approve or reject this customer?")}
    # The 01 graph approves low risk itself (reviewer "system"); a human answer adds reviewer_notes to the record.
    if risk.get("decision") == "approve" and values.get("reviewer") != "human" and "reviewer_notes" not in risk:
        return {"route": "Approved automatically (low risk)", "outcome": "approved"}
    if risk.get("decision"):
        kind = ((last or {}).get("resume") or {}).get("kind")
        route = {"edd": "EDD sign-off", "graph": "Human review (high risk)"}.get(kind, "Human decision")
        return {"route": route, "outcome": "approved" if risk["decision"] == "approve" else "rejected",
                "notes": risk.get("reviewer_notes") or values.get("reviewer_notes", "")}
    return {"route": None, "outcome": "waiting"}


def case_file(store, customer_id: str, name: str, live_policy: str, runnable: bool) -> dict:
    risk = memory.get_customer_risk(store, customer_id)
    client = memory.get_client(store, customer_id) or {}
    review = _kyc_review(store, customer_id)
    values = _graph_values(store, risk)
    stage = stage_of(store, customer_id, live_policy)
    last = memory.get_review(store, f"kyc:{customer_id}")
    approval = _approval(risk, review, values, last if last and last.get("status") == "closed" else None)
    reasons = (risk or {}).get("reasons", [])
    refresh = next((f for f in memory.open_refresh_flags(store) if f["customer_id"] == customer_id), None)
    onboarded = bool(client.get("onboarded"))
    screened = risk is not None

    def step(key, map_no, title, status, **evidence):
        return {"key": key, "map": map_no, "title": title, "status": status, **evidence}

    decided = approval["outcome"] in ("approved", "rejected")
    monitoring_status = ("waiting" if stage in ("reassessment_due", "refresh_due") else
                         "active" if onboarded and stage == "onboarded" else "todo")
    steps = [
        step("start", "START", "Application received", "done" if screened else "current",
             application=values.get("application", "")),
        step("profile", "3", "Profile extracted from the application", "done" if values.get("profile") else
             ("done" if screened else "todo"), profile=values.get("profile") or {}),
        step("screen", "5", "Screening: sanctions, PEP and stated connections",
             "done" if screened else "todo", hits=values.get("screening_hits", []),
             connections=[r for r in reasons if r["text"].startswith("Stated ")]),
        step("risk", "6", "Risk assessment", "done" if screened else "todo",
             rating=(risk or {}).get("rating"), policy=(risk or {}).get("policy"), reasons=reasons,
             previous=(risk or {}).get("previous")),
        step("approval", "7", "Approval", "waiting" if approval["outcome"] == "waiting" and screened else
             "done" if decided else "todo", **approval,
             memo=(risk or {}).get("memo") or values.get("decision_memo", "")),
        step("end", "END", "Onboarded" if approval["outcome"] == "approved" else
             "Rejected" if approval["outcome"] == "rejected" else "Onboarded or rejected",
             "done" if decided else "todo", outcome=approval["outcome"]),
        step("monitoring", "R1 · R2", "Ongoing monitoring", monitoring_status,
             assessed_policy=client.get("assessed_policy"), live_policy=live_policy,
             due_changes=client.get("due_changes", []), refresh=refresh, user_id=memory.user_for_customer(customer_id)),
    ]
    return {
        "customer_id": customer_id, "name": (risk or {}).get("name") or name,
        "user_id": memory.user_for_customer(customer_id), "runnable": runnable, "stage": stage,
        "next_action": NEXT_ACTION.get(stage, ""), "live_policy": live_policy, "rating": (risk or {}).get("rating"),
        "previous": (risk or {}).get("previous"), "policy": (risk or {}).get("policy"),
        "steps": steps, "not_used": NOT_USED, "history": client.get("history", []),
    }


def make_router(applicants: dict[str, str], runnable: set[str], live_policy: Callable[[], str],
                run_job: Callable[[str, Callable[[], None]], None] | None = None,
                screen: Callable[[str], None] | None = None) -> APIRouter:
    """Build the router with the portal's applicant register, runnable set, live-policy lookup, background-job runner
    and the function that screens one customer under the live policy."""
    router = APIRouter()

    def _bulk(stage: str, label: str):
        if run_job is None or screen is None:
            raise HTTPException(501, "Bulk screening is not wired up")
        store, live = memory.get_store(), live_policy()
        ids = [c for c in applicants if c in runnable and stage_of(store, c, live) == stage]
        if not ids:
            raise HTTPException(400, f"No {label} to run")

        def job():
            memory.log_event(memory.get_store(), "kyc", f"KYC: {label} for {len(ids)} client(s) started")
            for cid in ids:
                screen(cid)
            memory.log_event(memory.get_store(), "kyc", f"KYC: {label} finished for {len(ids)} client(s)")

        run_job(f"{label} ({len(ids)})", job)
        return {"started": len(ids), "customers": ids}

    @router.post("/api/kyc/screen-new")
    def screen_new():
        return _bulk("new", "screening of new applications")

    @router.post("/api/kyc/reassess-due")
    def reassess_due():
        return _bulk("reassessment_due", "re-assessment under the live policy")

    @router.get("/api/kyc/pipeline")
    def pipeline():
        store, live = memory.get_store(), live_policy()
        cases = []
        for cid, name in applicants.items():
            risk = memory.get_customer_risk(store, cid) or {}
            stage = stage_of(store, cid, live)
            cases.append({"customer_id": cid, "name": risk.get("name") or name, "stage": stage,
                          "rating": risk.get("rating"), "previous": risk.get("previous"), "policy": risk.get("policy"),
                          "has_card": bool(memory.user_for_customer(cid)), "runnable": cid in runnable,
                          "next_action": NEXT_ACTION.get(stage, "")})
        counts = {s["id"]: sum(c["stage"] == s["id"] for c in cases) for s in STAGES if s["count"]}
        bulk = {stage: sum(c["stage"] == stage and c["runnable"] for c in cases) for stage in ("new", "reassessment_due")}
        return {"live_policy": live, "stages": [{**s, "n": counts.get(s["id"])} for s in STAGES], "cases": cases,
                "bulk": bulk}

    @router.get("/api/kyc/case/{customer_id}")
    def case(customer_id: str):
        if customer_id not in applicants:
            raise HTTPException(404, f"No applicant {customer_id}")
        return case_file(memory.get_store(), customer_id, applicants[customer_id], live_policy(),
                         customer_id in runnable)

    @router.get("/kyc/kyc.js")
    def script():
        return FileResponse(STATIC / "kyc.js", media_type="application/javascript")

    @router.get("/kyc/kyc.css")
    def style():
        return FileResponse(STATIC / "kyc.css", media_type="text/css")

    return router
