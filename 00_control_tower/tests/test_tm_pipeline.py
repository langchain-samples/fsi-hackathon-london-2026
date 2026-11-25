"""Offline tests for the rebuilt 02 (Marcus): rules triage, routing, investigator and auditor, human review.

Run from the repo root: `uv run pytest -q 00_control_tower/tests/test_tm_pipeline.py`. The models are scripted
(control_tower/offline.py reads 02/data/offline_investigations.jsonl), so these check the plumbing and the code
controls, not model quality. The code fact check runs for real.
"""
from datetime import date

from control_tower import memory, offline
from control_tower.pillars import tm
from control_tower.policy import BASELINE, PS26_7_REFERENCE
from control_tower.reviews import answer_review

rules = tm.project()["tools.rules"]
db = tm.project()["utils.db"]
nodes = tm.project()["utils.nodes"]


def _decisions(store, user_id):
    return {i.key: i.value for i in store.search(("decisions", user_id), limit=100)}


def _rated(store, customer_id, rating):
    memory.put_customer_risk(store, customer_id, {"rating": rating, "reasons": [], "policy": "PS26-7"})


# --- step 3: triage in code ---

def test_bands_match_the_analyst_labels():
    """The hand-check from the MD: blocks land high, clear approvals low, both ambiguous cases medium."""
    expected = {"block": "high"}
    for label in db.table("analyst_labels"):
        tx = db.get("transactions", transaction_id=label["transaction_id"])
        band = rules.triage(tx, None, (30, 70))["band"]
        want = expected.get(label["decision"]) or ("medium" if label["ambiguous"] else "low")
        assert band == want, f"{label['transaction_id']}: {band}, expected {want}"


def test_tx_2004_triage_matches_the_spec():
    tx = db.get("transactions", transaction_id="TX-2004")
    t = rules.triage(tx, "high", (20, 70))
    assert t["score"] == 100 and t["band"] == "high" and t["cut_offs"] == [20, 70]
    assert [r["id"] for r in t["rules_fired"]] == ["R-01", "R-02", "R-04", "R-05"]
    assert "25 minutes apart" in t["rules_fired"][1]["detail"]


def test_high_risk_rules_and_cut_offs_come_from_the_rating_and_policy():
    tx = db.get("transactions", transaction_id="TX-2005")
    standard = rules.triage(tx, "low", tm.context_for("USR-002", "low", PS26_7_REFERENCE).grey_zone)
    high = rules.triage(tx, "high", tm.context_for("USR-002", "high", PS26_7_REFERENCE).grey_zone)
    assert (standard["score"], standard["cut_offs"]) == (30, [30, 70])
    assert (high["score"], high["cut_offs"]) == (50, [20, 70]) and "R-05" in [r["id"] for r in high["rules_fired"]]
    # Under the baseline policy a high-risk client keeps the standard cut-offs: only approved policy changes them.
    assert tm.context_for("USR-002", "high", BASELINE).grey_zone == (30, 70)


def test_amounts_are_converted_before_rules_run():
    assert rules.in_currency(100, "EUR", "GBP", {"GBP": 1.0, "EUR": 0.85}) == 85.0
    tx = db.get("transactions", transaction_id="TX-1004")  # EUR 64.50 against a GBP 10-150 range: not above usual
    assert rules.triage(tx)["rules_fired"] == []


def test_sample_is_stable_and_named():
    sample = rules.load_rule_set("retail_cards")["sample"]
    assert rules.is_sampled("TX-2002", sample)
    assert [rules.is_sampled(t, sample) for t in ("TX-1001", "TX-2003")] == \
           [rules.is_sampled(t, sample) for t in ("TX-1001", "TX-2003")]


# --- steps 4 to 8: the graph, through the control tower adapter ---

def test_every_medium_or_high_case_reaches_a_human():
    """Review routing, the safety metric: 100% under PS26-7 for a high-risk client."""
    store = memory.reset_store()
    _rated(store, "CUST-026", "high")
    answers = {"TX-2004": {"decision": "block", "note": "Impossible travel."},
               "TX-2005": {"decision": "approve", "note": "Booked a flight to a conference."}}
    tm.run_tm("USR-002", store, PS26_7_REFERENCE, reviewer_answers=answers, run_tag="routing")
    d = _decisions(store, "USR-002")
    assert len(d) == 5
    for record in d.values():
        if record["band"] in ("medium", "high"):
            assert record["decided_by"] == "human", record["transaction_id"]
        else:
            assert record["decided_by"] == "rules", record["transaction_id"]
    assert d["TX-2004"]["final_decision"] == "block" and d["TX-2004"]["audit"] == "pass"
    assert d["TX-2004"]["next_step"] == "block_and_escalate" and d["TX-2004"]["kyc_rating"] == "high"
    assert d["TX-2004"]["policy_version"] == "PS26-7" and d["TX-2004"]["cut_offs"] == [20, 70]
    # TX-2002 is the named sample: investigated, nothing to escalate, so approved by the rules with the report kept.
    assert d["TX-2002"]["sample"] and d["TX-2002"]["report"]["next_step"] == "close_explained"
    assert d["TX-2001"]["report"] is None  # an unsampled low payment never reaches a model


def test_reviewer_note_is_kept_with_an_expiry():
    store = memory.reset_store()
    answers = {"TX-2005": {"decision": "approve", "note": "Conference trip."}}
    tm.run_tm("USR-002", store, PS26_7_REFERENCE, transaction_ids=["TX-2005"], reviewer_answers=answers, run_tag="n")
    note = store.get(("customer_notes", "USR-002"), "TX-2005").value
    assert note["note"] == "Conference trip." and note["valid_until"] == "2026-11-09"


def test_refresh_flag_has_a_due_date_and_a_conduct_score():
    store = memory.reset_store()
    _rated(store, "CUST-026", "high")
    answers = {"TX-2004": {"decision": "block", "note": "Impossible travel."},
               "TX-2005": {"decision": "approve", "note": "Conference."}}
    tm.run_tm("USR-002", store, PS26_7_REFERENCE, reviewer_answers=answers, run_tag="refresh")
    flag = next(f for f in memory.open_refresh_flags(store) if f["customer_id"] == "CUST-026")
    assert flag["due_by"] == "2026-10-23"  # TX-2005 on Sat 10 Oct, plus 10 business days: Fri 23 Oct (5.5)
    summary = memory.get_tm_summary(store, "CUST-026")
    assert summary["score"] == "red" and any("blocked by a reviewer" in d for d in summary["drivers"])


def test_business_days_skip_weekends():
    assert tm.business_days_after(date(2026, 10, 9), 1) == date(2026, 10, 12)  # Friday -> Monday


def test_parked_case_resumes_with_the_same_settings():
    store = memory.reset_store()
    _rated(store, "CUST-026", "high")
    tm.run_tm("USR-002", store, PS26_7_REFERENCE, run_tag="park")
    case = memory.get_review(store, "tm:TX-2004")
    assert case and case["question"]["band"] == "high" and case["question"]["next_step"] == "block_and_escalate"
    assert any("R-02" in w for w in case["question"]["why_review"])
    answer_review(store, "tm:TX-2004", {"decision": "block", "note": "Card cloned."})
    d = _decisions(store, "USR-002")
    assert d["TX-2004"]["final_decision"] == "block" and d["TX-2004"]["cut_offs"] == [20, 70]
    assert memory.get_review(store, "tm:TX-2005")  # the run carried on and parked the next alert


# --- step 6: the auditor and the code fact check ---

def _state(tx_id):
    tx = db.get("transactions", transaction_id=tx_id)
    earlier = [e for e in db.query("transactions", user_id=tx["user_id"]) if e["timestamp"] < tx["timestamp"]]
    return {"transaction": tx, "earlier": earlier, "profile": db.get("customer_profiles", user_id=tx["user_id"])}


def test_fact_check_catches_seeded_faults():
    state = _state("TX-2004")
    good = {"evidence": [{"claim": "Lisbon first", "source": "TX-2003.country", "value": "PT"},
                         {"claim": "EUR 1,800", "source": "TX-2004.amount", "value": "EUR 1,800"},
                         {"claim": "usual range", "source": "profile.usual_amount_range", "value": "[5, 400]"}]}
    assert nodes.check_facts(good, state) == []
    wrong_country = {"evidence": [{"claim": "Madrid first", "source": "TX-2003.country", "value": "ES"}]}
    other_client = {"evidence": [{"claim": "crypto", "source": "TX-1005.category", "value": "crypto"}]}
    no_field = {"evidence": [{"claim": "VIP", "source": "profile.vip", "value": "yes"}]}
    for faulty in (wrong_country, other_client, no_field):
        assert len(nodes.check_facts(faulty, state)) == 1


def test_reviewer_notes_in_date_can_be_cited():
    state = {**_state("TX-2005"), "notes": [{"transaction_id": "TX-2004", "note": "Impossible travel. Card blocked."}]}
    cited = {"evidence": [{"claim": "Earlier block", "source": "TX-2004.reviewer_note", "value": "Impossible travel"}]}
    assert nodes.check_facts(cited, state) == []
    assert len(nodes.check_facts(cited, {**state, "notes": []})) == 1  # an expired note is not on file


def test_auditor_returns_a_faulty_report_at_most_twice(monkeypatch):
    faulty = {"transaction_id": "TX-2005", "summary": "Flight booked from Spain.", "assessment": "unclear",
              "evidence": [{"claim": "Paid in Spain", "source": "TX-2005.country", "value": "ES"}],
              "next_step": "request_information", "paragraphs": ["6.1"], "risk_score": 50, "confidence": 0.6}
    monkeypatch.setattr(offline, "_investigations", lambda: {"TX-2005": faulty})
    store = memory.reset_store()
    tm.run_tm("USR-002", store, PS26_7_REFERENCE, transaction_ids=["TX-2005"], run_tag="audit")
    case = memory.get_review(store, "tm:TX-2005")
    assert case["question"]["audit"]["verdict"] == "return"
    assert any("Auditor still objects after 2 return(s)" in w for w in case["question"]["why_review"])
    answer_review(store, "tm:TX-2005", {"decision": "approve", "note": "Client confirmed."})
    record = _decisions(store, "USR-002")["TX-2005"]
    assert record["investigations"] == 3 and record["audit_returns"] == 3 and record["decided_by"] == "human"
