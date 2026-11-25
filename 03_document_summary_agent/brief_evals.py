"""Pass/fail checks for the document summary agent's `Brief`, run locally or as a LangSmith experiment.

Run from this folder:

    uv run python brief_evals.py --local    # runs the agent on each example and prints the scores
    uv run python brief_evals.py            # same examples as a LangSmith dataset + experiment

Every check is plain code (no model judges), so a score always means the same thing:

- ids_sequential:        item ids run 1, 2, 3 ... with no gaps or repeats
- sources_well_formed:   every source reads like "PS26-7 para 4.3" and names the right document
- sources_exist:         every cited paragraph really exists in the document
- obligations_found:     share of the hand-picked must-have paragraphs that have an item
- key_facts_kept:        share of the hand-picked numbers, lists and deadlines that survive word for word
- no_non_obligations:    nothing cited from sections that set no obligations (introduction, definitions, contacts)
- firm_fields_filled:    with a firm given, every item says whether it applies to the firm
- firm_findings:         share of the hand-picked firm-specific findings the brief gets right (right deadline,
                         provider named, gap turned into an action, small-firm extensions marked as not applying)

The gold lists below were picked by hand from the documents in data/documents and the firm's data in
data/firms.jsonl. Extend them as you find gaps.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import re

from utils.db import DATA_DIR

SUFFIX = os.environ.get("ATTENDEE_NAME", os.environ.get("USER", "team"))
DATASET = f"doc-summary-brief-{SUFFIX}"

# --- gold examples -------------------------------------------------------------------------------------------

# Each fact is a list of acceptable spellings; any one of them counts.
EXAMPLES = [
    (
        {"document_id": "PS26-7", "firm_id": "FIRM-001"},
        {
            "must_cite": ["4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "5.1", "5.2", "5.3", "5.4", "5.5", "6.1", "6.2",
                          "6.3", "6.4", "6.5", "6.6", "7.1", "7.2", "7.3", "8.1", "8.2", "8.3", "9.1"],
            "key_facts": [["Valdoria"], ["Karvonia"], ["Ostmark"], ["£100,000", "100,000"], ["0.85"],
                          ["20 or more", "20 or above", "of 20"], ["£250"], ["2 or more", "two or more"],
                          ["30-day", "30 days"], ["10 business days"], ["30 June 2027"], ["2 business days"]],
            "never_cite": ["1", "3", "10"],
            # FIRM-001 has 1.2m customers and £9bn assets: not a small firm, so 9.2 and 9.3 don't apply.
            "firm_findings": [
                {"name": "small-firm extensions don't apply", "paras": ["9.2", "9.3"], "every_item_applies": "no"},
                {"name": "existing customers re-checked by 30 June 2027", "paras": ["9.1"], "deadline": "30 June 2027"},
                {"name": "first controls test by 30 September 2027", "paras": ["8.2"], "deadline": "30 September 2027"},
            ],
        },
    ),
    (
        {"document_id": "PS26-4", "firm_id": "FIRM-001"},
        {
            "must_cite": ["4.1", "4.2", "4.3", "4.5", "5.1", "5.3", "6.1", "6.2", "6.3", "6.4", "7.1", "7.2", "7.3",
                          "8.1", "8.2", "8.5"],
            "key_facts": [["31 March 2027"], ["4 hours"], ["72 hours"], ["60 days"], ["30 business days"],
                          ["31 January 2027"]],
            "never_cite": ["1", "3", "10"],
            # FIRM-001: no operational resilience senior manager; NimbusCloud contract ends 30 June 2027 and hosts
            # both mobile banking and card payments; PayCore contract ends 31 December 2029; not a small firm.
            "firm_findings": [
                {"name": "missing senior manager is an action", "paras": ["8.1"], "deadline": "31 January 2027",
                 "mentions": [["appoint", "not named", "no senior manager", "No operational resilience", "not recorded",
                               "none is named", "no named"]]},
                {"name": "NimbusCloud contract updated by 30 June 2027", "paras": ["9.3", "6.3"],
                 "mentions": [["NimbusCloud"], ["30 June 2027"]]},
                {"name": "PayCore contract updated by 31 December 2028", "paras": ["9.3", "6.3"],
                 "mentions": [["PayCore"], ["31 December 2028"]]},
                # "yes" or "unclear" are both fair: the data doesn't say both services are *important* ones.
                {"name": "NimbusCloud concentration risk", "paras": ["6.5"], "mentions": [["NimbusCloud"]]},
                {"name": "standard deadline, not the small-firm one", "paras": ["4.1"], "deadline": "31 March 2027"},
                {"name": "small-firm extensions don't apply", "paras": ["9.1", "9.2"], "every_item_applies": "no"},
            ],
        },
    ),
]

# --- target --------------------------------------------------------------------------------------------------

_agent = None


def brief_target(inputs: dict) -> dict:
    """Run the agent with the same request the control tower sends, and return its Brief as a dict."""
    global _agent
    if _agent is None:
        from agent import build_agent
        _agent = build_agent()
    request = f"Summarise document {inputs['document_id']} for {inputs['firm_id']}"
    result = _agent.invoke({"messages": [{"role": "user", "content": request}]})
    return {"brief": result["structured_response"].model_dump()}


# --- helpers -------------------------------------------------------------------------------------------------

PARA = re.compile(r"(\d+\.\d+)")


def _items(outputs: dict) -> list[dict]:
    return outputs["brief"]["items"]


def _cited(outputs: dict) -> set[str]:
    return {p for item in _items(outputs) for p in PARA.findall(item["source"])}


def _paragraphs_in(document_id: str) -> set[str]:
    text = (DATA_DIR / "documents" / f"{document_id}.md").read_text(encoding="utf-8")
    return set(re.findall(r"^(\d+\.\d+) ", text, flags=re.MULTILINE))


# --- evaluators ----------------------------------------------------------------------------------------------


def ids_sequential(outputs: dict) -> dict:
    ids = [item["id"] for item in _items(outputs)]
    return {"key": "ids_sequential", "score": int(bool(ids) and ids == list(range(1, len(ids) + 1)))}


def sources_well_formed(inputs: dict, outputs: dict) -> dict:
    pattern = re.compile(rf"^{re.escape(inputs['document_id'])} paras? \d+\.\d+")
    bad = [item["source"] for item in _items(outputs) if not pattern.match(item["source"])]
    return {"key": "sources_well_formed", "score": int(not bad), "comment": f"bad: {bad}" if bad else ""}


def sources_exist(inputs: dict, outputs: dict) -> dict:
    missing = sorted(_cited(outputs) - _paragraphs_in(inputs["document_id"]))
    return {"key": "sources_exist", "score": int(not missing), "comment": f"not in document: {missing}" if missing else ""}


def obligations_found(outputs: dict, reference_outputs: dict) -> dict:
    wanted = reference_outputs["must_cite"]
    missing = [p for p in wanted if p not in _cited(outputs)]
    return {"key": "obligations_found", "score": round(1 - len(missing) / len(wanted), 3),
            "comment": f"missing: {missing}" if missing else ""}


def key_facts_kept(outputs: dict, reference_outputs: dict) -> dict:
    text = " ".join(item["summary"] for item in _items(outputs)) + " " + outputs["brief"]["summary"]
    facts = reference_outputs["key_facts"]
    lost = [fact[0] for fact in facts if not any(spelling in text for spelling in fact)]
    return {"key": "key_facts_kept", "score": round(1 - len(lost) / len(facts), 3),
            "comment": f"lost: {lost}" if lost else ""}


def no_non_obligations(outputs: dict, reference_outputs: dict) -> dict:
    sections = set(reference_outputs["never_cite"])
    wrong = sorted(p for p in _cited(outputs) if p.split(".")[0] in sections)
    return {"key": "no_non_obligations", "score": int(not wrong), "comment": f"cited: {wrong}" if wrong else ""}


def firm_fields_filled(inputs: dict, outputs: dict) -> dict:
    if not inputs.get("firm_id"):
        return {"key": "firm_fields_filled", "score": 1}
    blank = [item["id"] for item in _items(outputs) if item.get("applies_to_firm") is None]
    return {"key": "firm_fields_filled", "score": int(not blank), "comment": f"blank ids: {blank}" if blank else ""}


def _finding_met(finding: dict, outputs: dict) -> bool:
    items = [item for item in _items(outputs) if set(PARA.findall(item["source"])) & set(finding["paras"])]
    if "every_item_applies" in finding:  # nothing wrongly applied (no items at all is fine)
        return all(item.get("applies_to_firm") == finding["every_item_applies"] for item in items)
    for item in items:
        text = f"{item.get('firm_deadline') or ''} {item.get('firm_action') or ''}".lower()
        if "applies" in finding and item.get("applies_to_firm") != finding["applies"]:
            continue
        if "deadline" in finding and finding["deadline"].lower() not in text:
            continue
        if all(any(s.lower() in text for s in group) for group in finding.get("mentions", [])):
            return True
    return False


def firm_findings(outputs: dict, reference_outputs: dict) -> dict:
    findings = reference_outputs.get("firm_findings", [])
    if not findings:
        return {"key": "firm_findings", "score": 1}
    missed = [f["name"] for f in findings if not _finding_met(f, outputs)]
    return {"key": "firm_findings", "score": round(1 - len(missed) / len(findings), 3),
            "comment": f"missed: {missed}" if missed else ""}


EVALUATORS = [ids_sequential, sources_well_formed, sources_exist, obligations_found, key_facts_kept,
              no_non_obligations, firm_fields_filled, firm_findings]

# --- runners -------------------------------------------------------------------------------------------------


def run_local(save_dir: str | None = None) -> None:
    for inputs, reference in EXAMPLES:
        outputs = brief_target(inputs)
        if save_dir:
            path = os.path.join(save_dir, f"brief_{inputs['document_id']}_{inputs['firm_id']}.json")
            with open(path, "w", encoding="utf-8") as file:
                json.dump(outputs["brief"], file, indent=2, ensure_ascii=False)
        args = {"inputs": inputs, "outputs": outputs, "reference_outputs": reference}
        print(f"\n== {inputs['document_id']} for {inputs['firm_id']}: {len(_items(outputs))} items")
        for ev in EVALUATORS:
            params = inspect.signature(ev).parameters
            result = ev(**{k: v for k, v in args.items() if k in params})
            print(f"  {result['key']:<22} {result['score']}  {result.get('comment', '')}")


def run_langsmith(prefix: str) -> None:
    from langsmith import Client
    client = Client()
    if not client.has_dataset(dataset_name=DATASET):
        dataset = client.create_dataset(DATASET, description="03 document summary agent: Brief gold set")
        client.create_examples(dataset_id=dataset.id,
                               examples=[{"inputs": i, "outputs": o} for i, o in EXAMPLES])
    client.evaluate(brief_target, data=DATASET, evaluators=EVALUATORS, experiment_prefix=prefix, max_concurrency=1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="print scores here instead of a LangSmith experiment")
    parser.add_argument("--prefix", default="doc-summary-brief")
    parser.add_argument("--save", metavar="DIR", help="with --local, also save each Brief as JSON in DIR")
    args = parser.parse_args()
    run_local(args.save) if args.local else run_langsmith(args.prefix)
