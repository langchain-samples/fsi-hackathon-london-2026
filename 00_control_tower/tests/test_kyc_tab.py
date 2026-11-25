"""KYC tab: the pipeline and case-file APIs place each case on the KYC process map. Offline (scripted model)."""
import os

os.environ["CONTROL_TOWER_OFFLINE"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from control_tower import memory  # noqa: E402
from control_tower.pillars import kyc, reg  # noqa: E402
from control_tower.policy import BASELINE, PS26_7_REFERENCE, diff  # noqa: E402
from portal.app import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_store():
    memory.reset_store()
    yield


def stages():
    return {c["customer_id"]: c["stage"] for c in client.get("/api/kyc/pipeline").json()["cases"]}


def steps(cid):
    return {s["key"]: s for s in client.get(f"/api/kyc/case/{cid}").json()["steps"]}


def test_every_applicant_starts_as_a_new_application():
    body = client.get("/api/kyc/pipeline").json()
    assert len(body["cases"]) == 27 and set(stages().values()) == {"new"}
    assert [s["map"] for s in body["stages"]][:5] == ["START", "3", "5", "6", "7"]
    assert next(s for s in body["stages"] if s["id"] == "new")["n"] == 27


def test_low_risk_case_walks_the_whole_map():
    kyc.run_kyc("CUST-027", memory.get_store(), BASELINE)
    assert stages()["CUST-027"] == "onboarded"
    s = steps("CUST-027")
    assert all(s[k]["status"] == "done" for k in ("start", "profile", "screen", "risk", "approval", "end"))
    assert s["monitoring"]["status"] == "active"
    assert s["approval"]["route"] == "Approved automatically (low risk)"
    assert s["profile"]["profile"]["full_name"] == "Priya Shah" and s["start"]["application"]


def test_pep_waits_for_a_human_and_the_decision_uses_the_action_queue():
    store = memory.get_store()
    kyc.run_kyc("CUST-002", store, BASELINE)  # Viktor Harlan, a PEP
    assert stages()["CUST-002"] == "approval"
    s = steps("CUST-002")
    assert s["approval"]["status"] == "waiting" and s["approval"]["case_id"] == "kyc:CUST-002"
    assert s["screen"]["hits"][0]["list"] == "pep"
    assert any(a["id"] == "kyc:CUST-002" for a in client.get("/api/state").json()["actions"])


def test_policy_change_moves_an_onboarded_client_to_reassessment_then_edd():
    store = memory.get_store()
    kyc.run_kyc("CUST-026", store, BASELINE)  # Tomás Ferreira: LOW under baseline
    assert stages()["CUST-026"] == "onboarded"
    memory.put_policy(store, PS26_7_REFERENCE.model_dump())
    memory.mark_policy_change(store, "PS26-7", diff(BASELINE, PS26_7_REFERENCE))
    assert stages()["CUST-026"] == "reassessment_due"
    assert steps("CUST-026")["monitoring"]["status"] == "waiting"

    kyc.run_kyc("CUST-026", store, reg.active_policy(store, memory.DEFAULT_FIRM))
    case = client.get("/api/kyc/case/CUST-026").json()
    s = {x["key"]: x for x in case["steps"]}
    assert case["stage"] == "approval" and case["rating"] == "high" and case["previous"]["rating"] == "low"
    assert s["approval"]["route"] == "EDD sign-off"
    assert any(r["paragraph"] for r in s["risk"]["reasons"])
    assert s["screen"]["connections"]  # the stated business partner who is a PEP

    memory_review = memory.get_review(store, "kyc:CUST-026")
    from control_tower.reviews import answer_review
    answer_review(store, memory_review["case_id"], {"decision": "approve", "notes": "EDD pack reviewed"})
    s = steps("CUST-026")
    assert stages()["CUST-026"] == "onboarded"
    assert s["approval"]["route"] == "EDD sign-off" and s["approval"]["notes"] == "EDD pack reviewed"


def test_transaction_alerts_put_a_client_in_refresh():
    store = memory.get_store()
    kyc.run_kyc("CUST-026", store, BASELINE)
    memory.flag_kyc_refresh(store, "CUST-026", "2 alerts in 30 days")
    assert stages()["CUST-026"] == "refresh_due"
    assert steps("CUST-026")["monitoring"]["refresh"]["reason"] == "2 alerts in 30 days"


def test_unknown_customer_and_static_files():
    assert client.get("/api/kyc/case/CUST-999").status_code == 404
    assert client.get("/kyc/kyc.js").status_code == 200 and client.get("/kyc/kyc.css").status_code == 200
    page = client.get("/").text
    assert 'id="kyc-flow"' in page and "/kyc/kyc.js" in page


def _wait():
    import time
    for _ in range(300):
        status = client.get("/api/state").json()["status"]
        if not status["running"]:
            assert not status["error"], status["error"]
            return
        time.sleep(0.1)
    raise AssertionError("background job did not finish")


def test_every_applicant_can_be_screened_offline():
    body = client.get("/api/kyc/pipeline").json()
    assert all(c["runnable"] for c in body["cases"]) and body["bulk"]["new"] == 27


def test_screen_all_new_then_reassess_all_due():
    assert client.post("/api/kyc/screen-new").json()["started"] == 27
    _wait()
    after = stages()
    assert "new" not in after.values()
    assert after["CUST-002"] == "approval" and after["CUST-016"] == "approval"   # PEP; sanctioned
    assert after["CUST-001"] == "onboarded" and after["CUST-026"] == "onboarded"
    assert client.post("/api/kyc/screen-new").status_code == 400                 # nothing left to screen

    store = memory.get_store()
    memory.put_policy(store, PS26_7_REFERENCE.model_dump())
    memory.mark_policy_change(store, "PS26-7", diff(BASELINE, PS26_7_REFERENCE))
    due = [c for c, s in stages().items() if s == "reassessment_due"]
    assert "CUST-026" in due and client.get("/api/kyc/pipeline").json()["bulk"]["reassessment_due"] == len(due)
    assert client.post("/api/kyc/reassess-due").json()["started"] == len(due)
    _wait()
    after = stages()
    assert "reassessment_due" not in after.values()
    assert after["CUST-026"] == "approval"      # Tomás: business partner of a PEP, EDD sign-off under PS26-7
    assert after["CUST-001"] == "onboarded"     # Sarah: nothing in PS26-7 touches her
