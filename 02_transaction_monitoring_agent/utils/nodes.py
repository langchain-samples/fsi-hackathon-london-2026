"""The nodes of the 02 graph: steps 2 to 8 of the transaction-monitoring process map.

    load_context (2, code) -> triage (3, code) -> route (4, code)
        low, not sampled           -> record_decision: approved by the rules, never seen by a model
        medium, high, or a sample  -> investigate (5, model) -> audit (6, model + code fact check)
                                      a returned report goes back to the investigator at most twice
    record_decision (7 human review via interrupt(), 8 record)

Code decides what is suspicious and what the policy requires; the models explain and check; a person decides every
alert. Neither model can approve or block a payment.
"""

import json
import os
import re
from datetime import date, datetime, timedelta

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from langsmith import Client, traceable
from langsmith.run_helpers import get_current_run_tree

from tools.rules import load_rule_set
from tools.rules import triage as run_rules
from utils import PROJECT_ROOT, db
from utils.schemas import AuditResult, InvestigationReport
from utils.state import MyAgentContext, TmState
from utils.transaction_processing import transaction_stream

INVESTIGATOR_PROMPT = (PROJECT_ROOT / "instructions.md").read_text(encoding="utf-8")
AUDITOR_PROMPT = (PROJECT_ROOT / "auditor.md").read_text(encoding="utf-8")
ALERT_WINDOW_DAYS = 30
ESCALATE_TO_BLOCK = ("block_and_escalate", "refer_mlro")


def _ctx(runtime: Runtime[MyAgentContext]) -> MyAgentContext:
    return runtime.context if runtime.context is not None else MyAgentContext()


def _text(message) -> str:
    text = getattr(message, "text", None)
    if isinstance(text, str):  # langchain-core 1.x: a property
        return str(text)
    if callable(text):  # older versions: a method
        text = text()
    return text if isinstance(text, str) else str(getattr(message, "content", ""))


def _day(ts: str) -> date:
    return datetime.fromisoformat(ts).date()


# --- step 2: load the client context --------------------------------------------------------------------------

def load_context(state: TmState, runtime: Runtime[MyAgentContext]) -> dict:
    """Look up the payment, the client's declared profile, earlier payments, notes still in date and recent alerts."""
    ctx = _ctx(runtime)
    human = [m for m in state["messages"] if getattr(m, "type", "") == "human"]
    found = re.search(r"TX-\d+", _text(human[-1])) if human else None
    reset = {"transaction_id": found.group(0) if found else "", "transaction": {}, "profile": {}, "earlier": [],
             "notes": [], "recent_alerts": [], "triage": {}, "report": None, "audit": None, "returns": [],
             "investigations": 0, "investigator_run": None, "decision": None}
    # Only this customer's transactions: the customer comes from the run's context, not from the message.
    tx = db.get("transactions", transaction_id=found.group(0), user_id=ctx.user_id) if found else None
    if tx is None:
        what = found.group(0) if found else "no transaction ID"
        return {**reset, "messages": [AIMessage(f"There is no transaction {what} for customer {ctx.user_id}.")]}

    earlier = [e for e in transaction_stream(ctx.user_id, end=tx["timestamp"]) if e["transaction_id"] != tx["transaction_id"]]
    notes, alerts = [], []
    if runtime.store is not None:
        for item in runtime.store.search(("customer_notes", ctx.user_id), limit=100):
            note = {"transaction_id": item.key, **item.value}
            # 6.6: explanations are reused on later payments, but an expired note never reaches the investigator.
            if not note.get("valid_until") or note["valid_until"] >= tx["timestamp"][:10]:
                notes.append(note)
        start = datetime.fromisoformat(tx["timestamp"]) - timedelta(days=ALERT_WINDOW_DAYS)
        alerts = [i.value for i in runtime.store.search(("alerts", ctx.user_id), limit=1000)
                  if start <= datetime.fromisoformat(i.value["timestamp"]) < datetime.fromisoformat(tx["timestamp"])]
    return {**reset, "transaction": tx, "profile": db.get("customer_profiles", user_id=ctx.user_id),
            "earlier": earlier, "notes": notes, "recent_alerts": sorted(alerts, key=lambda a: a["timestamp"])}


def after_load(state: TmState) -> str:
    return "triage" if state.get("transaction") else END


# --- step 3: triage, in code -----------------------------------------------------------------------------------

def triage(state: TmState, runtime: Runtime[MyAgentContext]) -> dict:
    ctx = _ctx(runtime)
    return {"triage": run_rules(state["transaction"], ctx.kyc_rating, tuple(ctx.grey_zone), ctx.rule_set,
                                state["profile"], state["earlier"])}


# --- step 4: route ---------------------------------------------------------------------------------------------

def after_triage(state: TmState) -> str:
    t = state["triage"]
    return "investigate" if t["band"] in ("medium", "high") or t["sample"] else "record_decision"


# --- step 5: the investigator ----------------------------------------------------------------------------------

def case_file(state: TmState, ctx: MyAgentContext) -> str:
    """Everything the investigator may rely on, in one message. It cites facts from here and nothing else."""
    t, config = state["triage"], load_rule_set(ctx.rule_set)
    medium, high = t["cut_offs"]
    rules = "\n".join(f"- {r['id']} {r['name']} (weight {r['weight']}, para {r['paragraph']}): {r['detail']}"
                      for r in t["rules_fired"]) or "- No rule fired."
    run_so_far = [_text(m) for m in state["messages"] if getattr(m, "type", "") == "ai"][-10:]
    notes = "\n".join(f"- {n['transaction_id']} (valid until {n.get('valid_until', 'not set')}; cite as "
                      f"`{n['transaction_id']}.reviewer_note`): {n['note']}" for n in state["notes"]) or "None."
    alerts = "\n".join(f"- {a['transaction_id']} on {a['timestamp']}: {a.get('decision')}"
                       for a in state["recent_alerts"]) or "None."
    paragraphs = "\n".join(f"- {k}: {v}" for k, v in config.get("paragraphs", {}).items())
    sections = [
        f"TRANSACTION_ID: {state['transaction_id']}",
        f"## Payment under review\n{json.dumps(state['transaction'], ensure_ascii=False)}",
        f"## Client profile (record `profile`)\n{json.dumps(state['profile'], ensure_ascii=False)}",
        f"## KYC\nRating: {(ctx.kyc_rating or 'none (treated as medium)').upper()}, under policy {ctx.policy_version}."
        + ("\nReasons:\n" + "\n".join(f"- {r}" for r in ctx.kyc_reasons) if ctx.kyc_reasons else ""),
        f"## Triage by the rules engine (code, rule set {t['rule_set']})\nScore {t['score']}, band {t['band'].upper()} "
        f"(medium from {medium}, high from {high})." + (" Picked by the below-the-line sample." if t["sample"] else "")
        + f"\n{rules}",
        "## Earlier payments by this client, oldest first\n"
        + ("\n".join(json.dumps(e, ensure_ascii=False) for e in state["earlier"][-10:]) or "None."),
        "## Earlier in this monitoring run\n" + ("\n".join(f"- {line}" for line in run_so_far) or "Nothing yet."),
        f"## Reviewer notes still in date (para 6.6)\n{notes}",
        f"## Alerts in the last {ALERT_WINDOW_DAYS} days\n{alerts}",
        f"## PS26-7 paragraphs you may cite\n{paragraphs}",
    ]
    if state.get("returns"):
        last = state["returns"][-1]
        sections.append("## The auditor returned your last report. Fix every point.\n"
                        + "\n".join(f"- Fact check failed: {f}" for f in last["facts_failed"])
                        + "".join(f"\n- {i}" for i in last["issues"])
                        + ("\n- Your wording could tip off the client (para 7.2)." if last["tipping_off"] else ""))
    return "\n\n".join(sections)


def make_investigate(llm):
    structured = llm.with_structured_output(InvestigationReport)

    @traceable(name="investigator", run_type="chain")
    def _investigate(messages):
        run = get_current_run_tree()
        return structured.invoke(messages), ({"id": str(run.id), "trace_id": str(run.trace_id),
                                              "project": run.session_name} if run else None)

    def investigate(state: TmState, runtime: Runtime[MyAgentContext]) -> dict:
        messages = [SystemMessage(INVESTIGATOR_PROMPT), HumanMessage(case_file(state, _ctx(runtime)))]
        report, run = _investigate(messages)
        report = report.model_dump() if hasattr(report, "model_dump") else dict(report)
        report["transaction_id"] = state["transaction_id"]  # the model cannot change which payment it is about
        return {"report": report, "investigations": state.get("investigations", 0) + 1, "investigator_run": run}

    return investigate


def after_investigate(state: TmState) -> str:
    """A sampled low payment reaches the auditor and a person only if the investigator proposes escalating it."""
    t, report = state["triage"], state["report"]
    if t["band"] == "low" and report["next_step"] == "close_explained":
        return "record_decision"
    return "audit"


# --- step 6: the auditor, with a code check on every cited fact -------------------------------------------------

def _norm(value) -> str:
    return re.sub(r"[\s,'\"]", "", str(value)).lower()


def same_value(actual, claimed) -> bool:
    """Does a value the investigator quoted match the record? Lenient on format, strict on substance."""
    if isinstance(actual, bool):
        return _norm(claimed) in ("true", "false") and (_norm(claimed) == "true") == actual
    if isinstance(actual, (int, float)):
        number = re.search(r"-?\d+(?:\.\d+)?", str(claimed).replace(",", ""))
        return bool(number) and abs(float(number.group()) - float(actual)) < 0.01
    if isinstance(actual, list):
        claimed_n = _norm(claimed)
        return claimed_n.strip("[]") == _norm(json.dumps(actual)).strip("[]") or \
            all(_norm(x) in claimed_n for x in actual)
    a, c = _norm(actual), _norm(claimed)
    return bool(c) and (c == a or (len(c) >= 2 and c in a))


def _records(state: TmState) -> dict:
    """The client's transactions the report may cite, with any reviewer note in date as a `reviewer_note` field."""
    notes = {n["transaction_id"]: n["note"] for n in state.get("notes", [])}
    return {t["transaction_id"]: {**t, **({"reviewer_note": notes[t["transaction_id"]]}
                                          if t["transaction_id"] in notes else {})}
            for t in [*state["earlier"], state["transaction"]]}


def check_facts(report: dict, state: TmState) -> list[str]:
    """Look up every evidence item's source field. A value that does not match its record fails."""
    records = _records(state)
    failed = []
    for ev in report.get("evidence", []):
        record_id, _, field = ev.get("source", "").partition(".")
        record = state["profile"] if record_id.lower() == "profile" else records.get(record_id)
        if record is None:
            failed.append(f"{ev['claim']} [{ev['source']}: no such record for this client]")
        elif field not in record:
            failed.append(f"{ev['claim']} [{ev['source']}: no such field]")
        elif not same_value(record[field], ev.get("value", "")):
            failed.append(f"{ev['claim']} [{ev['source']} is {record[field]!r}, the report says {ev['value']!r}]")
    return failed


def _cited_records(report: dict, state: TmState) -> dict:
    ids = {ev.get("source", "").partition(".")[0] for ev in report.get("evidence", [])}
    records = _records(state)
    return {"profile": state["profile"], **{i: records[i] for i in sorted(ids) if i in records}}


_client = None
_projects: dict[str, str] = {}


def _feedback(run: dict | None, audit: dict) -> None:
    """Attach the auditor's verdict to the investigator's run in LangSmith, so Engine and experiments can see it."""
    global _client
    if not run or os.environ.get("LANGSMITH_TRACING", "").lower() != "true":
        return
    try:
        _client = _client or Client()
        if run.get("project") and run["project"] not in _projects:
            _projects[run["project"]] = str(_client.read_project(project_name=run["project"]).id)
        _client.create_feedback(run["id"], key="auditor_verdict", trace_id=run["trace_id"],
                                session_id=_projects.get(run.get("project")),
                                score=1 if audit["verdict"] == "pass" else 0,
                                comment="; ".join([*audit["facts_failed"], *audit["issues"]]) or "pass")
    except Exception:  # feedback is evidence for later, never a reason to fail a payment decision
        pass


def make_audit(llm):
    structured = llm.with_structured_output(AuditResult)

    @traceable(name="auditor", run_type="chain")
    def _audit(messages):
        return structured.invoke(messages)

    def audit(state: TmState, runtime: Runtime[MyAgentContext]) -> dict:
        report, ctx = state["report"], _ctx(runtime)
        facts_failed = check_facts(report, state)
        config = load_rule_set(ctx.rule_set)
        audit_file = "\n\n".join([
            f"TRANSACTION_ID: {state['transaction_id']}",
            f"## The investigator's report\n{json.dumps(report, ensure_ascii=False, indent=1)}",
            f"## Raw records it cites\n{json.dumps(_cited_records(report, state), ensure_ascii=False, indent=1)}",
            "## Rules the code fired\n" + ("\n".join(f"- {r['id']} {r['name']} (para {r['paragraph']}): {r['detail']}"
                                                     for r in state["triage"]["rules_fired"]) or "- None."),
            "## PS26-7 paragraphs\n" + "\n".join(f"- {k}: {v}" for k, v in config.get("paragraphs", {}).items()),
            "## Code check on every cited fact\n" + ("\n".join(f"- FAILED: {f}" for f in facts_failed)
                                                       or "- Every cited value matches its record."),
        ])
        result = _audit([SystemMessage(AUDITOR_PROMPT), HumanMessage(audit_file)])
        result = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        # The code check is authoritative: the model may add failed facts, never remove one.
        result["facts_failed"] = list(dict.fromkeys([*facts_failed, *result.get("facts_failed", [])]))
        if result["facts_failed"] or result["tipping_off"]:
            result["verdict"] = "return"
        _feedback(state.get("investigator_run"), result)
        returns = [*state.get("returns", []), result] if result["verdict"] == "return" else state.get("returns", [])
        return {"audit": result, "returns": returns}

    return audit


def after_audit(state: TmState) -> str:
    """Back to the investigator at most `max_returns` times; after that the reviewer sees the objections."""
    if state["audit"]["verdict"] == "pass":
        return "record_decision"
    max_returns = load_rule_set("retail_cards").get("max_returns", 2)
    return "investigate" if len(state.get("returns", [])) <= max_returns else "record_decision"


# --- steps 7 and 8: human review, then record --------------------------------------------------------------------

def proposed_decision(report: dict | None) -> str:
    """What the investigator's next step implies for the payment. A proposal only: a person decides."""
    return "block" if report and report["next_step"] in ESCALATE_TO_BLOCK else "approve"


def review_question(state: TmState, ctx: MyAgentContext) -> dict:
    t, report, audit = state["triage"], state.get("report"), state.get("audit")
    medium, high = t["cut_offs"]
    why = [f"Band {t['band']}: rules score {t['score']} (medium from {medium}, high from {high})"]
    why += [f"{r['id']} {r['name']}: {r['detail']} (para {r['paragraph']})" for r in t["rules_fired"]]
    if t["sample"]:
        why.append("Below-the-line sample: the investigator proposed escalating a low-band payment")
    if report and report["confidence"] < ctx.confidence_threshold:
        why.append(f"Investigator unsure: confidence {report['confidence']} is below {ctx.confidence_threshold}")
    if audit and audit["verdict"] == "return":
        why.append(f"Auditor still objects after {len(state.get('returns', [])) - 1} return(s)")
        why += [f"Fact check failed: {f}" for f in audit["facts_failed"]] + list(audit["issues"])
    return {
        "transaction_id": state["transaction_id"], "risk_score": t["score"],
        "confidence": report["confidence"] if report else 1.0, "band": t["band"], "cut_offs": t["cut_offs"],
        "rules_fired": t["rules_fired"], "why_review": why, "agent_decision": proposed_decision(report),
        "next_step": report["next_step"] if report else None, "assessment": report["assessment"] if report else None,
        "reason": report["summary"] if report else "", "report": report, "audit": audit,
        "question": "Approve or block? Reply with {'decision': 'approve' or 'block', 'note': 'optional "
                    "explanation, kept for later payments (para 6.6)'}",
    }


def _answer(value) -> dict:
    """Studio and the API can deliver the answer as a string: accept JSON or a bare decision word."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {"decision": value.strip().strip("\"'")}
    decision = str(value.get("decision", "")).lower()
    return {**value, "decision": "block" if decision in ("block", "reject") else "approve"}


def record_decision(state: TmState, runtime: Runtime[MyAgentContext]) -> dict:
    """Step 7, a person decides every medium or high payment (and any sample the investigator escalated); low ones
    are approved by the rules. Step 8, record the decision with its evidence in the store."""
    ctx, t, report, audit = _ctx(runtime), state["triage"], state.get("report"), state.get("audit")
    tx = state["transaction"]
    note, valid_until = "", None
    by_rules = t["band"] == "low" and (report is None or report["next_step"] == "close_explained")
    if by_rules:
        final, decided_by = "approve", "rules"
        reason = f"Rules engine: band low (score {t['score']}, medium from {t['cut_offs'][0]})"
        if t["sample"]:
            reason += "; sampled, and the investigator found nothing to escalate"
    else:
        answer = _answer(interrupt(review_question(state, ctx)))
        final, decided_by = answer["decision"], "human"
        note = (answer.get("note") or answer.get("notes") or "").strip()
        reason = note or (report["summary"] if report else f"Reviewer decision: {final}")
        if note and runtime.store is not None:
            days = load_rule_set(ctx.rule_set).get("review_notes", {}).get("valid_days", 30)
            valid_until = (_day(tx["timestamp"]) + timedelta(days=days)).isoformat()
            runtime.store.put(("customer_notes", ctx.user_id), tx["transaction_id"],
                              {"note": note, "valid_until": valid_until, "recorded_on": tx["timestamp"][:10]})

    record = {
        "transaction_id": tx["transaction_id"], "timestamp": tx["timestamp"], "risk_score": t["score"],
        "confidence": report["confidence"] if report and not by_rules else 1.0,
        "final_decision": final, "decided_by": decided_by, "reason": reason,
        "band": t["band"], "cut_offs": t["cut_offs"], "sample": t["sample"],
        "rules_fired": [r["id"] for r in t["rules_fired"]], "rules_detail": t["rules_fired"],
        "next_step": report["next_step"] if report else None, "assessment": report["assessment"] if report else None,
        "agent_decision": proposed_decision(report) if report else None,
        "audit": audit["verdict"] if audit else None, "audit_detail": audit, "audit_returns": len(state.get("returns", [])),
        "investigations": state.get("investigations", 0), "report": report,
        "kyc_rating": ctx.kyc_rating, "rule_set": t["rule_set"], "policy_version": ctx.policy_version,
        "reviewer_note": note or None, "note_valid_until": valid_until,
    }
    if runtime.store is not None:
        runtime.store.put(("decisions", ctx.user_id), tx["transaction_id"], record)
    summary = (f"{tx['transaction_id']}: {final} by {decided_by} (band {t['band']}, score {t['score']}"
               + (", sampled" if t["sample"] else "") + (f"; investigator proposed {report['next_step']}" if report else "")
               + (f"; audit {audit['verdict']}" if audit else "") + ").")
    return {"decision": record, "messages": [AIMessage(summary)]}
