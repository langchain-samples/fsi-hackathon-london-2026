"""Offline rehearsal mode: a scripted stand-in for the LLM, so the whole story runs with no API key.

Use it to test the wiring tonight and to rehearse the demo if the venue Wi-Fi or the gateway fails. Every answer
it gives comes from the synthetic data in the repo (analyst labels, fixtures below), so it shows the plumbing, not
model quality. Turn it on with `CONTROL_TOWER_OFFLINE=1` or by calling `enable()`. Never present its output as a
real model run.
"""
from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda

from control_tower.loader import REPO_ROOT
from control_tower.policy import PS26_7_REFERENCE

PROFILES = {
    "Sarah Mitchell": {"full_name": "Sarah Mitchell", "date_of_birth": "1990-03-14", "nationality": "British",
                       "occupation": "Software engineer", "source_of_funds": "Salary"},
    "Tomas Verretti": {"full_name": "Tomas Verretti", "date_of_birth": "1980-02-04", "nationality": "Valdorian",
                       "occupation": "Member of Parliament", "source_of_funds": "Salary, vehicle sale"},
    "Tomás Ferreira": {"full_name": "Tomás Ferreira", "date_of_birth": "1986-06-21", "nationality": "Portuguese",
                       "occupation": "Software consultant", "source_of_funds": "Consultancy dividends"},
    "Priya Shah": {"full_name": "Priya Shah", "date_of_birth": "1991-05-02", "nationality": "British",
                   "occupation": "Product designer", "source_of_funds": "Salary"},
    "Viktor Harlan": {"full_name": "Viktor Harlan", "date_of_birth": "1968-07-02", "nationality": "Valdorian",
                      "occupation": "Deputy Minister of Finance", "source_of_funds": "Government salary, property sale"},
}
FACTS = {
    "Sarah Mitchell": {"connected_people": [], "country_of_residence": "United Kingdom",
                       "initial_deposit_amount": None, "initial_deposit_currency": None},
    "Tomas Verretti": {"connected_people": [], "country_of_residence": "Valdoria",
                       "initial_deposit_amount": 120000, "initial_deposit_currency": "USD"},
    "Tomás Ferreira": {"connected_people": [{"name": "Viktor Harlan", "relationship": "business partner"}],
                       "country_of_residence": "Portugal", "initial_deposit_amount": 60000,
                       "initial_deposit_currency": "EUR"},
    "Priya Shah": {"connected_people": [], "country_of_residence": "United Kingdom",
                   "initial_deposit_amount": None, "initial_deposit_currency": None},
    "Viktor Harlan": {"connected_people": [], "country_of_residence": "Valdoria",
                      "initial_deposit_amount": 250000, "initial_deposit_currency": "USD"},
}


# Every other applicant, so the rehearsal can screen all 27 (hand-checked against 01_kyc_agent/data/applications.jsonl).
# The five fixtures above keep precedence; this file repeats them so it lists every applicant once.
APPLICANTS_FILE = REPO_ROOT / "00_control_tower" / "data" / "offline_applicants.jsonl"
_APPLICANTS = [json.loads(line) for line in APPLICANTS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
for _row in _APPLICANTS:
    PROFILES.setdefault(_row["profile"]["full_name"], _row["profile"])
    FACTS.setdefault(_row["profile"]["full_name"], _row["facts"])


def covered_customers() -> set[str]:
    """Customer IDs the scripted model can answer for."""
    return {row["customer_id"] for row in _APPLICANTS}


def _labels() -> dict:
    path = REPO_ROOT / "02_transaction_monitoring_agent" / "data" / "analyst_labels.jsonl"
    return {row["transaction_id"]: row for row in map(json.loads, path.read_text().splitlines()) if row}


def _who(text: str) -> str:
    """The applicant a prompt is about: the known name that appears first (an application can name a partner)."""
    found = [(text.find(name), name) for name in PROFILES if name in text]
    return min(found)[1] if found else "Priya Shah"


def _investigations() -> dict:
    """Marcus's fixture: scripted investigator reports for the 02 graph, one per transaction it may investigate."""
    path = REPO_ROOT / "02_transaction_monitoring_agent" / "data" / "offline_investigations.jsonl"
    return {row["transaction_id"]: row for row in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if row}


def _investigation(text: str) -> dict:
    tx_id = re.search(r"TRANSACTION_ID: (TX-\d+)", text).group(1)
    return _investigations().get(tx_id) or {
        "transaction_id": tx_id, "summary": "Scripted (offline rehearsal): nothing unusual for this client.",
        "evidence": [], "assessment": "likely_legitimate", "next_step": "close_explained", "paragraphs": ["6.1"],
        "risk_score": 10, "confidence": 0.9}


class ScriptedModel(BaseChatModel):
    """Answers the prompts the control tower and the three projects send, from fixtures."""

    tools: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self.model_copy(update={"tools": list(tools)})

    def with_structured_output(self, schema, **kwargs):
        def answer(prompt: Any):
            text = prompt if isinstance(prompt, str) else str(prompt)
            name = schema.__name__
            if name == "CustomerProfile":
                return schema(**PROFILES[_who(text)])
            if name == "StatedFacts":
                return schema(**FACTS[_who(text)])
            if name in ("PolicyParams", "PolicyExtraction"):
                return PS26_7_REFERENCE.model_copy()
            if name == "InvestigationReport":  # the 02 investigator
                return schema(**_investigation(text))
            if name == "AuditResult":  # the 02 auditor passes; the code fact check still runs for real
                return schema(verdict="pass", facts_failed=[], issues=[], tipping_off=False)
            raise NotImplementedError(f"No script for {name}")
        return RunnableLambda(answer)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    def _reply(self, messages) -> AIMessage:
        if not self.tools:  # a plain prompt, e.g. the KYC decision memo
            return AIMessage(content="Decision memo (offline rehearsal): see the screening results and decision.")
        names = {getattr(t, "name", None) for t in self.tools}
        if "record_decision" in names:
            return self._tm_turn(messages)
        return AIMessage(content="Offline rehearsal mode: the supervisor does not plan without a real model.")

    def _tm_turn(self, messages) -> AIMessage:
        last_user = max(i for i, m in enumerate(messages) if m.type == "human")
        tx_id = re.search(r"TX-\d+", messages[last_user].content).group(0)
        done = [m.name for m in messages[last_user:] if isinstance(m, ToolMessage)]
        if "get_transaction" not in done:
            return AIMessage(content="", tool_calls=[{"name": "get_transaction", "args": {"transaction_id": tx_id},
                                                      "id": f"{tx_id}-1"}])
        if "get_customer_profile" not in done:
            return AIMessage(content="", tool_calls=[{"name": "get_customer_profile", "args": {}, "id": f"{tx_id}-2"}])
        if "record_decision" not in done:
            label = _labels().get(tx_id, {"risk_score": 10, "decision": "approve", "ambiguous": False})
            confidence = 0.6 if label["ambiguous"] else 0.9
            return AIMessage(content="", tool_calls=[{"name": "record_decision", "id": f"{tx_id}-3", "args": {
                "transaction_id": tx_id, "risk_score": label["risk_score"], "confidence": confidence,
                "decision": label["decision"], "reason": "Scripted from the analyst label (offline rehearsal)."}}])
        return AIMessage(content=f"{tx_id}: {[m for m in messages if isinstance(m, ToolMessage)][-1].content}")


def enable() -> None:
    """Route every model call in the three projects and the control tower to `ScriptedModel`."""
    from control_tower.pillars import kyc, tm

    fake = ScriptedModel()
    kyc_project = kyc.load_project(kyc.FOLDER)
    kyc_project["utils.nodes"].init_chat_model = lambda *a, **k: fake
    tm.project()["agent"].init_chat_model = lambda *a, **k: fake
    kyc.init_chat_model = lambda *a, **k: fake
    import control_tower.policy as policy
    policy.init_chat_model = lambda *a, **k: fake
