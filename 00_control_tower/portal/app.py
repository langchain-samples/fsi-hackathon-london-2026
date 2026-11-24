"""Control Tower portal: the 5-minute demo screen.

    uv run uvicorn portal.app:app --port 8000            # live models, traced to LangSmith
    CONTROL_TOWER_OFFLINE=1 uv run uvicorn portal.app:app --port 8000   # scripted rehearsal, no API key

The page drives the story one step at a time, shows the shared memory as it changes, and lets a reviewer answer
the cases the pillars park. Steps run in a background thread; the page polls /api/state.
"""
from __future__ import annotations

import os
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from control_tower import memory
from control_tower.loader import REPO_ROOT
from control_tower.pillars import kyc, reg, tm
from control_tower.policy import BASELINE, diff
from control_tower.reviews import answer_review

OFFLINE = os.environ.get("CONTROL_TOWER_OFFLINE") == "1"
if OFFLINE:
    from control_tower import offline
    offline.enable()

DEMO_CUSTOMERS = ["CUST-026", "CUST-027"]
DOCUMENT, FIRM = "PS26-7", memory.DEFAULT_FIRM
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="FinCrime Control Tower")
_lock = threading.Lock()
_status = {"running": None, "error": None}


def _store():
    return memory.get_store()


def _policy():
    return reg.active_policy(_store(), FIRM)


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


def _tag() -> str:
    return "after" if _policy().source_document != "baseline" else "before"


STEPS = {
    "onboard": lambda: [kyc.run_kyc(c, _store(), _policy()) for c in DEMO_CUSTOMERS],
    "monitor": lambda: [tm.run_tm(memory.user_for_customer(c), _store(), _policy(), run_tag=_tag())
                        for c in DEMO_CUSTOMERS],
    "regulation": lambda: (
        reg.publish_policy_from_brief(DOCUMENT, (REPO_ROOT / "03_document_summary_agent/data/documents"
                                                 / f"{DOCUMENT}.md").read_text(), FIRM, _store())
        if OFFLINE else reg.run_reg(DOCUMENT, FIRM, _store())),
    "refresh": lambda: [kyc.run_kyc(f["customer_id"], _store(), _policy())
                        for f in memory.open_refresh_flags(_store())],
}


class Answer(BaseModel):
    decision: str
    note: str = ""


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/api/step/{name}")
def step(name: str):
    if name not in STEPS:
        raise HTTPException(404, f"No step {name}")
    _run(name, STEPS[name])
    return {"started": name}


@app.post("/api/review/{case_id}")
def review(case_id: str, answer: Answer):
    payload = {"decision": answer.decision, "notes": answer.note, "note": answer.note}
    _run(f"review {case_id}", lambda: answer_review(_store(), case_id, payload))
    return {"started": case_id}


@app.post("/api/reset")
def reset():
    if _status["running"]:
        raise HTTPException(409, "A step is still running")
    memory.reset_store()
    tm._agents.clear()
    return {"reset": True}


@app.get("/api/state")
def state(after: int = 0):
    store, policy = _store(), _policy()
    customers = []
    for cid in DEMO_CUSTOMERS:
        user_id = memory.user_for_customer(cid)
        decisions = {i.key: i.value for i in store.search(("decisions", user_id), limit=100)}
        txs = tm.project()["utils.transaction_processing"].transaction_stream(user_id)
        risk = memory.get_customer_risk(store, cid)
        ctx = tm.context_for(user_id, (risk or {}).get("rating"), policy)
        customers.append({
            "customer_id": cid, "user_id": user_id, "risk": risk,
            "thresholds": {"confidence": ctx.confidence_threshold, "grey_zone": list(ctx.grey_zone)},
            "transactions": [{**t, "decision": decisions.get(t["transaction_id"])} for t in txs],
            "alerts": memory.alerts_for(store, user_id),
            "refresh": any(f["customer_id"] == cid for f in memory.open_refresh_flags(store)),
        })
    project = os.environ.get("LANGSMITH_PROJECT", "")
    return {
        "offline": OFFLINE, "status": _status, "policy": policy.model_dump(),
        "policy_changes": diff(BASELINE, policy), "customers": customers,
        "reviews": memory.reviews(store), "events": memory.events(store, after),
        "langsmith_project": project,
    }
