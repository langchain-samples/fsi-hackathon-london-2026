"""Contract tests: does each pillar still give the supervisor what it needs?

Pillar owners: run this after changing your agent (from the repo root):

    uv run pytest -q 00_control_tower/tests/test_contracts.py

A failure names the field that changed. Either keep that field, or tell Lukman so the adapter can follow.
Runs offline with a scripted model, so it checks the shape of your agent's output, not the quality of its answers.
"""
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from control_tower import memory
from control_tower.contracts import (KycOutput, TmContextFields, TmDecision, brief_from_result, read_policy,
                                     reg_request)
from control_tower.loader import load_project
from control_tower.pillars import kyc, reg, tm
from control_tower.policy import PS26_7_REFERENCE


# --- 01 KYC (Nam) ---

def test_kyc_graph_output_matches_contract():
    store = memory.reset_store()
    for customer_id in ("CUST-001", "CUST-026", "CUST-027"):
        config = {"configurable": {"thread_id": f"contract-{customer_id}"}}
        kyc.graph(store).invoke({"customer_id": customer_id}, config)
        state = KycOutput(**kyc.graph(store).get_state(config).values)
        assert state.profile.full_name


def test_kyc_paused_for_human_still_matches_contract():
    store = memory.reset_store()
    config = {"configurable": {"thread_id": "contract-pep"}}
    result = kyc.graph(store).invoke({"customer_id": "CUST-002"}, config)  # Viktor Harlan, a PEP
    assert "__interrupt__" in result, "a PEP should pause for a human"
    state = KycOutput(**kyc.graph(store).get_state(config).values)
    assert state.risk_level == "high" and state.decision is None


def test_kyc_input_is_just_a_customer_id():
    schema = kyc.graph().get_input_jsonschema()
    assert "customer_id" in schema["properties"]


def test_pillars_can_read_the_live_policy():
    store = memory.reset_store()
    memory.put_policy(store, PS26_7_REFERENCE.model_dump())
    assert read_policy(kyc.graph(store).store)["source_document"] == "PS26-7"
    assert read_policy(tm.agent_for(store).store)["high_risk_confidence_threshold"] == 0.85


# --- 02 Transaction monitoring (Marcus) ---

def test_tm_context_accepts_the_fields_the_supervisor_sets():
    ctx = tm.context_for("USR-002", "high", PS26_7_REFERENCE)
    TmContextFields(user_id=ctx.user_id, confidence_threshold=ctx.confidence_threshold, grey_zone=ctx.grey_zone)


def test_tm_decisions_match_contract():
    store = memory.reset_store()
    answers = {"TX-2005": {"decision": "approve", "note": "Conference trip."}}
    tm.run_tm("USR-002", store, PS26_7_REFERENCE, reviewer_answers=answers, run_tag="contract")
    records = [i.value for i in store.search(("decisions", "USR-002"), limit=100)]
    assert len(records) == 5
    for record in records:
        TmDecision(**record)


# --- 03 Regulatory change (Alejandro) ---

def test_reg_agent_builds_and_takes_the_supervisors_request():
    agent = reg.reg_agent()
    assert "messages" in agent.get_input_jsonschema()["properties"]
    assert "PS26-7" in reg_request("PS26-7", "FIRM-001")["messages"][0]["content"]


def test_reg_documents_are_registered():
    db = load_project(reg.FOLDER)["utils.db"]
    ids = {row["document_id"] for row in db.table("documents")}
    assert {"PS26-4", "PS26-7"} <= ids


def test_brief_accepted_as_text_or_structured():
    assert brief_from_result({"messages": [AIMessage(content="Brief: section 4 ...")]}) == "Brief: section 4 ..."

    class Brief(BaseModel):
        sections: list[str]

    structured = brief_from_result({"messages": [AIMessage(content="")], "structured_response": Brief(sections=["A"])})
    assert '"sections"' in structured
