"""Screen a name against the watchlists in the bank's database (`data/sanctions_list.jsonl` and `data/pep_list.jsonl`).

These are plain Python functions, with no LLM involved. In a real bank they would call a screening provider.
"""

from difflib import SequenceMatcher

from utils import db

MATCH_THRESHOLD = 0.85


def name_similarity(a: str, b: str) -> float:
    """Score how alike two names are, from 0 (nothing in common) to 1 (identical)."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def check_sanctions(name: str, date_of_birth: str) -> list[dict]:
    """Return the sanctions list entries whose name closely matches."""
    hits = []
    for entry in db.table("sanctions_list"):
        score = name_similarity(name, entry["name"])
        if score >= MATCH_THRESHOLD:
            hits.append(
                {
                    "list": "sanctions",
                    "matched_name": entry["name"],
                    "score": round(score, 2),
                    "date_of_birth_matches": entry["date_of_birth"] == date_of_birth,
                    "details": entry["program"],
                }
            )
    return hits


def check_pep(name: str) -> list[dict]:
    """Return the PEP list entries whose name closely matches."""
    hits = []
    for entry in db.table("pep_list"):
        score = name_similarity(name, entry["name"])
        if score >= MATCH_THRESHOLD:
            hits.append(
                {
                    "list": "pep",
                    "matched_name": entry["name"],
                    "score": round(score, 2),
                    "details": f"{entry['position']}, {entry['country']}",
                }
            )
    return hits
