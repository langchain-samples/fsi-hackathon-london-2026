"""LangSmith evals for the control tower: does regulation reach the controls, correctly and safely?

    uv run python -m evals.control_tower_evals            # creates datasets and runs experiments in LangSmith
    uv run python -m evals.control_tower_evals --local    # runs the same targets and evaluators locally, offline

Three datasets (suffixed with ATTENDEE_NAME so they don't clash in a shared workspace):
- policy-extraction: PS26-7 in, PolicyParams out; scored field by field against the hand-checked reference.
- kyc-rating: customer + policy in, rating out; gold labels to be extended by Nam to all 25 applications.
- end-to-end: the Tomás story; checks the rating, the refresh loop, citations and that no high-risk customer is
  approved without a human.

The per-pillar evals live with each pillar: Alejandro's gold set for 03, Marcus's Module 2/3 for 02.
"""
from __future__ import annotations

import argparse
import os

from control_tower import memory
from control_tower.pillars import kyc, reg, tm
from control_tower.policy import BASELINE, PS26_7_REFERENCE, PolicyParams

SUFFIX = os.environ.get("ATTENDEE_NAME", os.environ.get("USER", "team"))
POLICIES = {"baseline": BASELINE, "PS26-7": PS26_7_REFERENCE}

# --- datasets -------------------------------------------------------------------------------------------

POLICY_EXAMPLES = [({"document_id": "PS26-7", "firm_id": "FIRM-001"}, {"policy": PS26_7_REFERENCE.model_dump()})]

# Expected ratings. Nam: extend this to all 25 applications in 01_kyc_agent/data/applications.jsonl.
KYC_EXAMPLES = [
    ({"customer_id": "CUST-001", "policy": "PS26-7"}, {"rating": "low"}),
    ({"customer_id": "CUST-002", "policy": "PS26-7"}, {"rating": "high"}),   # PEP, Valdorian, large deposit
    ({"customer_id": "CUST-013", "policy": "PS26-7"}, {"rating": "high"}),   # PEP (name spelt differently)
    ({"customer_id": "CUST-026", "policy": "baseline"}, {"rating": "low"}),  # name screening misses the partner
    ({"customer_id": "CUST-026", "policy": "PS26-7"}, {"rating": "high"}),   # 4.3: stated business partner of a PEP
    ({"customer_id": "CUST-027", "policy": "PS26-7"}, {"rating": "low"}),
]

E2E_EXAMPLES = [({"customer_id": "CUST-026"}, {"rating": "high", "refresh": True})]

REVIEWER_ANSWERS = {
    "TX-1003": {"decision": "approve", "note": "On holiday in Portugal from 9 to 16 October."},
    "TX-2005": {"decision": "approve", "note": "Booked a flight to a conference in Berlin."},
}

# --- targets --------------------------------------------------------------------------------------------


def policy_target(inputs: dict) -> dict:
    store = memory.reset_store()
    out = reg.run_reg(inputs["document_id"], inputs["firm_id"], store, auto_approve=True)
    return {"policy": out["policy"]}


def kyc_target(inputs: dict) -> dict:
    store = memory.reset_store()
    record = kyc.run_kyc(inputs["customer_id"], store, POLICIES[inputs["policy"]])
    return {"rating": record["rating"], "reasons": record["reasons"], "status": record["status"],
            "decision": record["decision"]}


def e2e_target(inputs: dict) -> dict:
    store = memory.reset_store()
    memory.put_policy(store, PS26_7_REFERENCE.model_dump())
    policy = reg.active_policy(store)
    record = kyc.run_kyc(inputs["customer_id"], store, policy)
    user_id = memory.user_for_customer(inputs["customer_id"])
    tm.run_tm(user_id, store, policy, reviewer_answers=REVIEWER_ANSWERS, run_tag="eval")
    return {"rating": record["rating"], "reasons": record["reasons"], "status": record["status"],
            "decision": record["decision"],
            "refresh": any(f["customer_id"] == inputs["customer_id"] for f in memory.open_refresh_flags(store))}

# --- evaluators -----------------------------------------------------------------------------------------


def policy_field_accuracy(outputs: dict, reference_outputs: dict) -> dict:
    got, want = PolicyParams(**outputs["policy"]), PolicyParams(**reference_outputs["policy"])
    fields = [f for f in want.citations]
    norm = lambda v: sorted(str(x).lower() for x in v) if isinstance(v, list) else v  # noqa: E731
    right = sum(norm(getattr(got, f)) == norm(getattr(want, f)) for f in fields)
    return {"key": "policy_field_accuracy", "score": right / len(fields)}


def policy_citation_accuracy(outputs: dict, reference_outputs: dict) -> dict:
    got, want = outputs["policy"]["citations"], reference_outputs["policy"]["citations"]
    para = lambda s: {p.strip() for p in str(s).replace("para", "").split(",") if p.strip()}  # noqa: E731
    right = sum(bool(para(got.get(f, "")) & para(p)) for f, p in want.items())
    return {"key": "policy_citation_accuracy", "score": right / len(want)}


def rating_correct(outputs: dict, reference_outputs: dict) -> dict:
    return {"key": "rating_correct", "score": int(outputs["rating"] == reference_outputs["rating"])}


def high_risk_reasons_cited(outputs: dict) -> dict:
    """Every high-risk reason added by policy must cite a paragraph (PS26-7 5.2)."""
    policy_reasons = [r for r in outputs["reasons"] if r["level"] != "low" and "match:" not in r["text"]]
    return {"key": "reasons_cited", "score": int(all(r["paragraph"] for r in policy_reasons))}


def no_unreviewed_high_risk_approval(outputs: dict) -> dict:
    """Safety: a high-risk customer is never approved by the system alone (PS26-7 4.6)."""
    unsafe = outputs["rating"] == "high" and outputs.get("status") == "approved" and outputs.get("decision") == "approve" \
        and not outputs.get("reviewer_notes")
    return {"key": "no_unreviewed_high_risk_approval", "score": int(not unsafe)}


def refresh_loop_fired(outputs: dict, reference_outputs: dict) -> dict:
    return {"key": "refresh_loop_fired", "score": int(outputs["refresh"] == reference_outputs["refresh"])}


SUITES = {
    "policy-extraction": (POLICY_EXAMPLES, policy_target, [policy_field_accuracy, policy_citation_accuracy]),
    "kyc-rating": (KYC_EXAMPLES, kyc_target, [rating_correct, high_risk_reasons_cited, no_unreviewed_high_risk_approval]),
    "end-to-end": (E2E_EXAMPLES, e2e_target, [rating_correct, refresh_loop_fired, high_risk_reasons_cited,
                                               no_unreviewed_high_risk_approval]),
}


def run_local(names: list[str]) -> None:
    import inspect
    for name in names:
        examples, target, evaluators = SUITES[name]
        print(f"\n== {name}")
        for inputs, reference in examples:
            outputs = target(inputs)
            scores = []
            for ev in evaluators:
                args = {"outputs": outputs, "reference_outputs": reference}
                params = inspect.signature(ev).parameters
                scores.append(ev(**{k: v for k, v in args.items() if k in params}))
            print(inputs, {s["key"]: s["score"] for s in scores})


def run_langsmith(names: list[str], prefix: str) -> None:
    from langsmith import Client
    client = Client()
    for name in names:
        examples, target, evaluators = SUITES[name]
        dataset = f"control-tower-{name}-{SUFFIX}"
        if not client.has_dataset(dataset_name=dataset):
            ds = client.create_dataset(dataset, description=f"Control tower {name} gold set")
            client.create_examples(dataset_id=ds.id, examples=[{"inputs": i, "outputs": o} for i, o in examples])
        client.evaluate(target, data=dataset, evaluators=evaluators, experiment_prefix=f"{prefix}-{name}",
                        max_concurrency=1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="run offline with the scripted model")
    parser.add_argument("--suite", choices=list(SUITES), action="append")
    parser.add_argument("--prefix", default="control-tower")
    args = parser.parse_args()
    if args.local:
        from control_tower import offline
        offline.enable()
        run_local(args.suite or ["kyc-rating", "end-to-end"])  # policy-extraction needs the real 03 agent
    else:
        run_langsmith(args.suite or list(SUITES), args.prefix)
