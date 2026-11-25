"""The demo story, end to end, from the command line.

    uv run python scenario.py             # real models through the LangSmith gateway, traced to LangSmith
    uv run python scenario.py --offline   # scripted model, no API key: tests the wiring and rehearses the demo

Story: Tomás Ferreira's application says his business partner is Viktor Harlan, a PEP. Name screening misses that.
1. Before PS26-7: KYC rates him low and approves him; TM watches him with default thresholds.
2. PS26-7 lands: the regulatory pillar briefs it and proposes control changes; a reviewer approves them.
3. After: KYC rates him high (para 4.1, 4.3) and holds him for EDD sign-off; TM uses stricter thresholds (6.3);
   two alerts in 30 days trigger a KYC refresh (6.5).
"""
import argparse
import json
import os

from control_tower import memory
from control_tower.loader import REPO_ROOT
from control_tower.pillars import kyc, reg, tm
from control_tower.reviews import answer_review

CUSTOMER, USER = "CUST-026", "USR-002"


def show(title, value=None):
    print(f"\n== {title}")
    if value is not None:
        print(json.dumps(value, indent=2, default=str, ensure_ascii=False)[:1500])


def main(offline: bool) -> dict:
    if offline:
        from control_tower import offline as off
        off.enable()
    store = memory.reset_store()
    answers = {"TX-2005": {"decision": "approve", "note": "Booked a flight to a conference in Berlin."},
               "TX-2004": {"decision": "block", "note": "Impossible travel: Lisbon then Singapore in 25 minutes."}}

    # 1. Before PS26-7
    before = kyc.run_kyc(CUSTOMER, store, reg.active_policy(store))
    show("KYC before PS26-7", {k: before[k] for k in ("rating", "status", "reasons")})
    tm_before = tm.run_tm(USER, store, reg.active_policy(store), reviewer_answers=answers, run_tag="before")
    show("TM before PS26-7", [(d["transaction_id"], d["final_decision"], d["decided_by"]) for d in tm_before])

    # 2. PS26-7 lands
    if offline:  # the 03 deep agent needs a real model; feed the document text in as the brief instead
        brief = (REPO_ROOT / "03_document_summary_agent/data/documents/PS26-7.md").read_text()
        proposal = reg.publish_policy_from_brief("PS26-7", brief, memory.DEFAULT_FIRM, store)
    else:
        proposal = reg.run_reg("PS26-7", memory.DEFAULT_FIRM, store)
    show("PS26-7 proposes", proposal["changes"])
    answer_review(store, proposal["case_id"], {"decision": "approve", "notes": "Checked against PS26-7."})

    # 3. After PS26-7
    tm.start_replay(store, USER)  # replay the same transactions under the new policy, like for like
    after = kyc.run_kyc(CUSTOMER, store, reg.active_policy(store))
    show("KYC after PS26-7", {k: after[k] for k in ("rating", "status", "reasons")})
    tm_after = tm.run_tm(USER, store, reg.active_policy(store), reviewer_answers=answers, run_tag="after")
    show("TM after PS26-7", [(d["transaction_id"], d["final_decision"], d["decided_by"]) for d in tm_after])
    show("KYC refresh flags", memory.open_refresh_flags(store))
    show("Open reviews", [r["case_id"] for r in memory.reviews(store)])
    client = memory.get_client(store, CUSTOMER)
    show(f"Client file for {CUSTOMER}: stage {client['stage']}",
         [f"{h['what']}" + (f" (para {h['paragraph']})" if h["paragraph"] else "") for h in client["history"]])
    return {"client": client, "before": before, "after": after, "tm_before": tm_before, "tm_after": tm_after,
            "flags": memory.open_refresh_flags(store), "reviews": memory.reviews(store)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", default=os.environ.get("CONTROL_TOWER_OFFLINE") == "1")
    main(parser.parse_args().offline)
