"""Offline tests for the control tower wiring. Run from 00_control_tower: `uv run pytest -q`."""
from control_tower import memory, offline  # noqa: E402
from control_tower.loader import load_project  # noqa: E402
from control_tower.pillars import kyc, tm  # noqa: E402
from control_tower.policy import BASELINE, PS26_7_REFERENCE, diff  # noqa: E402
from control_tower.pillars.kyc import ConnectedPerson, StatedFacts, policy_overlay  # noqa: E402



def test_projects_load_side_by_side():
    a = load_project("01_kyc_agent")["utils.db"].DATA_DIR
    b = load_project("02_transaction_monitoring_agent")["utils.db"].DATA_DIR
    assert a != b and a.parent.name == "01_kyc_agent" and b.parent.name == "02_transaction_monitoring_agent"


def test_close_associate_is_high_only_under_ps26_7():
    check_pep = load_project("01_kyc_agent")["tools.screening"].check_pep
    facts = StatedFacts(connected_people=[ConnectedPerson(name="Viktor Harlan", relationship="business partner")],
                        country_of_residence="Portugal", initial_deposit_amount=60000, initial_deposit_currency="EUR")
    profile = {"nationality": "Portuguese"}
    assert policy_overlay(profile, facts, BASELINE, check_pep) == []
    reasons = policy_overlay(profile, facts, PS26_7_REFERENCE, check_pep)
    assert [r["level"] for r in reasons] == ["high"] and reasons[0]["paragraph"] == "4.1, 4.3"


def test_high_risk_country_and_source_of_funds():
    check_pep = load_project("01_kyc_agent")["tools.screening"].check_pep
    facts = StatedFacts(connected_people=[], country_of_residence="Valdoria", initial_deposit_amount=250000,
                        initial_deposit_currency="USD")
    levels = sorted(r["level"] for r in policy_overlay({"nationality": "Valdorian"}, facts, PS26_7_REFERENCE, check_pep))
    assert levels == ["high", "medium"]


def test_tm_thresholds_follow_kyc_rating():
    assert tm.context_for("USR-002", "low", PS26_7_REFERENCE).confidence_threshold == 0.7
    ctx = tm.context_for("USR-002", "high", PS26_7_REFERENCE)
    assert ctx.confidence_threshold == 0.85 and ctx.grey_zone == (20, 70)


def test_merchant_hold_and_alert_window():
    tx = {"category": "crypto", "amount": 400, "currency": "GBP"}
    assert tm.merchant_hold(tx, PS26_7_REFERENCE) and not tm.merchant_hold(tx, BASELINE)
    alerts = [{"timestamp": "2026-10-01T10:00"}, {"timestamp": "2026-10-09T09:35"}]
    assert tm.count_recent_alerts(alerts, "2026-10-10T11:00", 30) == 2
    assert tm.count_recent_alerts(alerts, "2026-11-20T11:00", 30) == 0


def test_policy_diff_cites_paragraphs():
    changes = {c["field"]: c["paragraph"] for c in diff(BASELINE, PS26_7_REFERENCE)}
    assert changes["alerts_for_kyc_refresh"] == "6.5" and changes["high_risk_confidence_threshold"] == "6.3"


def test_story_end_to_end_offline():
    import scenario
    out = scenario.main(offline=True)
    assert out["before"]["rating"] == "low" and out["before"]["status"] == "approved"
    assert out["after"]["rating"] == "high" and out["after"]["status"] == "pending_review"
    assert out["after"]["previous"] == {"rating": "low", "policy": "baseline"}
    assert any(f["customer_id"] == "CUST-026" for f in out["flags"])
    assert [r["case_id"] for r in out["reviews"]] == ["kyc:CUST-026"]


def test_client_file_follows_the_lifecycle():
    import scenario
    client = scenario.main(offline=True)["client"]
    stages = [h["what"] for h in client["history"] if h["pillar"] == "lifecycle"]
    assert stages[0] == "Stage: onboarding -> active"
    assert "Stage: active -> reassessment_due" in stages          # PS26-7 approved, nothing re-run yet
    assert client["stage"] == "under_review" and client["assessed_policy"] == "PS26-7"
    assert client["due_changes"] == []                            # cleared by the re-assessment
    cited = {h["paragraph"] for h in client["history"]}
    assert {"4.1, 4.3", "6.3", "6.5"} <= cited


def test_policy_change_marks_only_clients_assessed_under_the_old_policy():
    store = memory.reset_store()
    memory.record_client(store, "CUST-026", "KYC: rated LOW", "kyc", assessed_policy="baseline")
    memory.record_client(store, "CUST-027", "KYC: rated LOW", "kyc", assessed_policy="PS26-7")
    marked = memory.mark_policy_change(store, "PS26-7", diff(BASELINE, PS26_7_REFERENCE))
    assert marked == ["CUST-026"]
    fields = [c["field"] for c in memory.get_client(store, "CUST-026")["due_changes"]]
    assert "edd_for_pep_family_and_associates" in fields and memory.get_client(store, "CUST-027")["due_changes"] == []


def test_supervisor_builds():
    from control_tower.supervisor import build_supervisor
    graph = build_supervisor(model=offline.ScriptedModel())
    assert "tools" in graph.get_graph().nodes
