"""Check the dataset fits together before using it for evals. No model calls, so it's instant and free.

Run from the_friendly_fraudsters/:  uv run python -m evals.check_data
"""

from typing import get_args

from utils import db
from utils.nodes import load_case, triage
from utils.state import ReasonCode

ROUTES = {"fraud", "merchant", "friendly_fraud", "fast_lane"}
RECOMMENDATIONS = {"file_chargeback", "refund_customer", "decline_claim", None}
LEVELS = {"none", "low", "medium", "high"}
TYPES = {"health", "life_event", "low_resilience", "low_capability"}


def check() -> list[str]:
    problems = []
    customers = {c["customer_id"] for c in db.table("customers")}
    transactions = {t["transaction_id"]: t for t in db.table("transactions")}
    seen = set()

    for c in db.table("complaints"):
        cid = c["complaint_id"]
        if cid in seen:
            problems.append(f"{cid}: duplicate complaint_id")
        seen.add(cid)

        tx = transactions.get(c["transaction_id"])
        if c["customer_id"] not in customers:
            problems.append(f"{cid}: customer {c['customer_id']} not in customers.jsonl")
        if tx is None:
            problems.append(f"{cid}: transaction {c['transaction_id']} not in transactions.jsonl")
            continue
        if tx["customer_id"] != c["customer_id"]:
            problems.append(f"{cid}: transaction belongs to {tx['customer_id']}, not {c['customer_id']}")

        if c["expected_reason_code"] not in get_args(ReasonCode):
            problems.append(f"{cid}: unknown reason code {c['expected_reason_code']}")
        if c["expected_route"] not in ROUTES:
            problems.append(f"{cid}: unknown route {c['expected_route']}")
        if c["expected_recommendation"] not in RECOMMENDATIONS:
            problems.append(f"{cid}: unknown recommendation {c['expected_recommendation']}")

        vuln = c["expected_vulnerability"]
        if vuln["level"] not in LEVELS or not set(vuln["types"]) <= TYPES:
            problems.append(f"{cid}: bad vulnerability label {vuln}")
        if vuln["level"] == "high" and c["expected_route"] != "fast_lane":
            problems.append(f"{cid}: high vulnerability must route to fast_lane")
        if (vuln["level"] == "none") != (not vuln["types"]):
            problems.append(f"{cid}: vulnerability level and types disagree")

        # The triage rules must agree with the route label (fast lane is decided before triage).
        if c["expected_route"] != "fast_lane":
            state = {"complaint_id": cid}
            state |= load_case(state)
            state["reason_code"] = c["expected_reason_code"]
            got = triage(state)["route"]
            if got != c["expected_route"]:
                problems.append(f"{cid}: triage rules give '{got}' but label says '{c['expected_route']}'")

    return problems


if __name__ == "__main__":
    problems = check()
    complaints = db.table("complaints")
    print(f"{len(complaints)} complaints checked")
    for route in sorted(ROUTES):
        print(f"  {route}: {sum(c['expected_route'] == route for c in complaints)}")
    print("\n".join(problems) if problems else "All good: every case fits together.")
