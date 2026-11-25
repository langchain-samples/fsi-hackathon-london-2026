"""The rules engine: step 3 (triage) of the 02 process, in code.

Code decides what is suspicious; the models only explain and check. Each rule is a small function below. Its
parameters, weight, paragraph and the client bands it applies to live in a rule set config in `rules/` (for example
`rules/retail_cards.yaml`), so the same engine can run another profile by pointing at another file.

    triage(transaction, kyc_rating, cut_offs) -> TriageResult as a dict: score, band, cut-offs, sample, rules fired

The band cut-offs come from the run context (`MyAgentContext.grey_zone`, which the control tower sets from the
approved policy), never from the rule set file and never from a model.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from functools import lru_cache

import yaml

from utils import PROJECT_ROOT, db
from utils.transaction_processing import transaction_stream

RULES_DIR = PROJECT_ROOT / "rules"


# --- helpers ---------------------------------------------------------------------------------------------------

def to_gbp(amount: float, currency: str, fx: dict) -> float:
    return round(amount * fx.get(currency.upper(), 1.0), 2)


def in_currency(amount: float, currency: str, target: str, fx: dict) -> float:
    """Convert through GBP, so a EUR payment is never compared with a GBP range unconverted."""
    if currency.upper() == target.upper():
        return amount
    return round(to_gbp(amount, currency, fx) / fx.get(target.upper(), 1.0), 2)


def _money(amount: float, currency: str) -> str:
    return f"{currency} {amount:,.0f}"


def _when(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def _words(category: str) -> str:
    return category.replace("_", " ")


# --- the rules: each returns a plain-English detail when it fires, or None ---------------------------------------

def abroad_above_usual(tx, profile, earlier, params, fx):
    amount = in_currency(tx["amount"], tx["currency"], profile["currency"], fx)
    usual_max = profile["usual_amount_range"][1]
    if tx["country"] != profile["home_country"] and amount > usual_max:
        return (f"{_money(tx['amount'], tx['currency'])} in {tx['country']}; usual maximum "
                f"{_money(usual_max, profile['currency'])}")
    return None


def impossible_travel(tx, profile, earlier, params, fx):
    channel, hours = params.get("channel", "in_person"), params.get("within_hours", 6)
    if tx["channel"] != channel:
        return None
    now = _when(tx["timestamp"])
    for prev in reversed(earlier):
        gap = now - _when(prev["timestamp"])
        if gap > timedelta(hours=hours):
            break
        if prev["channel"] == channel and prev["country"] != tx["country"]:
            minutes = int(gap.total_seconds() // 60)
            return (f"In person in {prev['country']} at {prev['timestamp'][11:16]} ({prev['transaction_id']}), "
                    f"{tx['country']} at {tx['timestamp'][11:16]}: {minutes} minutes apart")
    return None


def high_risk_merchant(tx, profile, earlier, params, fx):
    gbp = to_gbp(tx["amount"], tx["currency"], fx)
    limit = params.get("limit_gbp", 250)
    if tx["category"] in params.get("categories", []) and gbp > limit:
        return f"GBP {gbp:,.0f} to {_words(tx['category'])}; limit GBP {limit:,}"
    return None


def new_category_above_usual(tx, profile, earlier, params, fx):
    seen = set(profile.get("usual_categories", [])) | {e["category"] for e in earlier}
    amount = in_currency(tx["amount"], tx["currency"], profile["currency"], fx)
    usual_max = profile["usual_amount_range"][1]
    if tx["category"] not in seen and amount > usual_max:
        return (f"First {_words(tx['category'])} payment, {_money(amount, profile['currency'])}; usual maximum "
                f"{_money(usual_max, profile['currency'])}")
    return None


def above_usual_max(tx, profile, earlier, params, fx):
    amount = in_currency(tx["amount"], tx["currency"], profile["currency"], fx)
    usual_max = profile["usual_amount_range"][1]
    if amount > usual_max:
        return f"{_money(amount, profile['currency'])}; usual maximum {_money(usual_max, profile['currency'])}"
    return None


RULES = {fn.__name__: fn for fn in (abroad_above_usual, impossible_travel, high_risk_merchant,
                                    new_category_above_usual, above_usual_max)}


# --- rule sets ---------------------------------------------------------------------------------------------------

@lru_cache(maxsize=8)
def load_rule_set(name: str = "retail_cards") -> dict:
    """A rule set config from `rules/<name>.yaml`, each rule marked runnable (its function exists) or not."""
    config = yaml.safe_load((RULES_DIR / f"{name}.yaml").read_text(encoding="utf-8"))
    for rule in config["rules"]:
        rule.setdefault("params", {})
        rule.setdefault("applies_to", ["standard", "high_risk"])
        rule["runnable"] = rule["fn"] in RULES
    return config


def rule_sets() -> list[dict]:
    """Every rule set in `rules/`, for display."""
    return [load_rule_set(p.stem) for p in sorted(RULES_DIR.glob("*.yaml"))]


# --- triage ------------------------------------------------------------------------------------------------------

def client_band(kyc_rating: str | None) -> str:
    """Which rules apply. High-risk clients get the high-risk rules; no rating is treated as standard (medium)."""
    return "high_risk" if (kyc_rating or "").lower() == "high" else "standard"


def is_sampled(transaction_id: str, sample: dict) -> bool:
    """A fixed share of payments, chosen by a stable hash of the ID so runs repeat; plus any named in the config."""
    if transaction_id in sample.get("named", []):
        return True
    bucket = int(hashlib.sha256(transaction_id.encode()).hexdigest(), 16) % 1000
    return bucket < round(sample.get("rate", 0) * 1000)


def band_for(score: int, cut_offs: tuple[int, int]) -> str:
    medium, high = cut_offs
    return "high" if score >= high else "medium" if score >= medium else "low"


def triage(tx: dict, kyc_rating: str | None = None, cut_offs: tuple[int, int] = (30, 70),
           rule_set: str = "retail_cards", profile: dict | None = None, earlier: list[dict] | None = None) -> dict:
    """Score one transaction with the rule set. Pure code: the same input always gives the same answer."""
    config = load_rule_set(rule_set)
    fx = config.get("fx_to_gbp", {})
    profile = profile or db.get("customer_profiles", user_id=tx["user_id"])
    if earlier is None:
        earlier = [e for e in transaction_stream(tx["user_id"], end=tx["timestamp"])
                   if e["transaction_id"] != tx["transaction_id"]]
    applies = client_band(kyc_rating)
    fired = []
    for rule in config["rules"]:
        if not rule["runnable"] or applies not in rule["applies_to"]:
            continue
        detail = RULES[rule["fn"]](tx, profile, earlier, rule["params"], fx)
        if detail:
            fired.append({"id": rule["id"], "name": rule["name"], "weight": rule["weight"],
                          "paragraph": rule["paragraph"], "detail": detail})
    score = min(100, sum(r["weight"] for r in fired))
    cut_offs = (int(cut_offs[0]), int(cut_offs[1]))
    band = band_for(score, cut_offs)
    return {"transaction_id": tx["transaction_id"], "score": score, "band": band, "cut_offs": list(cut_offs),
            "sample": band == "low" and is_sampled(tx["transaction_id"], config.get("sample", {})),
            "rules_fired": fired, "rule_set": config["rule_set"], "client_band": applies}
