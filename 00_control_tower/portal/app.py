"""Control Tower portal: the 5-minute demo screen.

    uv run uvicorn portal.app:app --port 8000            # live models, traced to LangSmith
    CONTROL_TOWER_OFFLINE=1 uv run uvicorn portal.app:app --port 8000   # scripted rehearsal, no API key

The page drives the story one step at a time, shows the shared memory as it changes, and lets a reviewer answer
the cases the pillars park. Steps run in a background thread; the page polls /api/state.

The story buttons call the pillar adapters directly, so the rehearsed demo does not depend on the supervisor. The
chat box talks to the supervisor (skills over the same store) for questions about a client's lifecycle. The chat
cannot record a human decision: those go through the review inbox only.
"""
from __future__ import annotations

import os
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

OFFLINE = os.environ.get("CONTROL_TOWER_OFFLINE") == "1"
if OFFLINE:
    from control_tower import offline
    offline.enable()
    build_supervisor = None   # the chat needs a real model; offline it is switched off, not faked
else:
    from langgraph.checkpoint.memory import InMemorySaver
    from control_tower.supervisor import build_supervisor

DEMO_CUSTOMERS = ["CUST-026", "CUST-027"]
DOCUMENT, FIRM = "PS26-7", memory.DEFAULT_FIRM
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="FinCrime Control Tower")
_lock = threading.Lock()
_status = {"running": None, "error": None}
_chat = {"log": [], "thread": uuid.uuid4().hex, "agent": None}


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


@app.post("/api/chat")
def chat(question: Question):
    if OFFLINE:
        raise HTTPException(400, "The chat needs live models. It is switched off in offline rehearsal.")
    if not question.message.strip():
        raise HTTPException(400, "Empty message")

    def work():
        try:
            _ask_supervisor(question.message)
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
    _chat.update(log=[], thread=uuid.uuid4().hex)
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
            "client": {**client, "stage": memory.client_stage(store, cid)} if (client := memory.get_client(store, cid))
            else None,
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
        "langsmith_project": project, "chat": _chat["log"],
    }
