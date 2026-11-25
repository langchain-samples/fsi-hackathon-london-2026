"""Test sets for the quality reviewer (Pradeep) and the customer letter agent (Wasiu).

    from evals.test_sets import qa_packs, letter_cases, check_letter, score_reviewer

- qa_packs(): 8 evidence packs (2 good, 6 with one planted mistake), each with the verdict the reviewer should give
  and the step it should send the case back to.
- letter_cases(): 8 letter scenarios with rules for what each letter must and must not say.
- check_letter(letter_text, case): the rules this letter breaks (empty list = passes).
- score_reviewer(review_fn): runs your reviewer over every pack and prints how many it got right.
"""

from utils import db


def qa_packs() -> list[dict]:
    return db.table("qa_test_packs")


def letter_cases() -> list[dict]:
    return db.table("letter_test_cases")


def check_letter(letter_text: str, case: dict) -> list[str]:
    """Code-based checks only (fast and free). Tone and clarity still need an LLM judge or a human."""
    text = letter_text.lower()
    problems = [f"Missing: {r['rule']}" for r in case["must_include"]
                if not any(phrase.lower() in text for phrase in r["any_of"])]
    problems += [f"Must not say: '{phrase}'" for phrase in case["must_not_include"] if phrase.lower() in text]
    return problems


def score_reviewer(review_fn) -> dict:
    """review_fn(pack) must return a dict with 'verdict' ('pass' or 'fail') and 'send_back_to'."""
    verdict_ok = route_ok = 0
    packs = qa_packs()
    for pack in packs:
        result = review_fn(pack)
        v = result.get("verdict") == pack["expected_verdict"]
        r = result.get("send_back_to") == pack["expected_send_back_to"]
        verdict_ok += v
        route_ok += r
        print(f"{pack['pack_id']} ({pack['expected_issue']}): verdict {'OK' if v else 'WRONG'}, send back {'OK' if r else 'WRONG'}")
    print(f"\nVerdict correct: {verdict_ok}/{len(packs)} · Sent to the right step: {route_ok}/{len(packs)}")
    return {"verdict_correct": verdict_ok, "route_correct": route_ok, "total": len(packs)}
