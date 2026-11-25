"""The firm's facts for one document, with the arithmetic done in code rather than by the model.

`get_firm_facts` joins the firm's profile (`get_firm_profile`) with what can be worked out exactly:

- whether the firm is a "small firm" under the document's own size test (both PS26-4 and PS26-7 define one in 2.3),
  which decides whether the small-firm deadlines in the transitional arrangements apply;
- its named senior managers, and gaps: roles or fields with nobody or nothing recorded, which become actions;
- its important business services, the providers each depends on, and any provider that several important services
  share (a concentration of risk).

The model then only has to apply these facts, never to compare numbers or invent missing data.
"""
import re

from utils import db

from .documents import get_document
from .firms import get_firm_profile

SIZE_TEST = re.compile(
    r"fewer than ([\d,]+) customers and total assets of less than £([\d.]+) (billion|million)", re.IGNORECASE)
SENIOR_MANAGER_FIELDS = {
    "operational_resilience_senior_manager": "operational resilience",
    "financial_crime_senior_manager": "financial crime",
}


def _gbp_millions(amount: str) -> float | None:
    """'£9 billion' -> 9000.0, '£500 million' -> 500.0; None if it can't be read."""
    match = re.search(r"£\s*([\d.,]+)\s*(billion|million|bn|m)\b", amount, re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    return value * 1000 if match.group(2).lower() in ("billion", "bn") else value


def _size_test(document_id: str, firm: dict) -> dict:
    document = get_document(document_id)
    for section in document["sections"]:
        for line in section["text"].splitlines():
            match = SIZE_TEST.search(line)
            if not match:
                continue
            paragraph = line.split(" ", 1)[0]
            customer_limit = int(match.group(1).replace(",", ""))
            asset_limit = float(match.group(2)) * (1000 if match.group(3).lower() == "billion" else 1)
            assets = _gbp_millions(firm["total_assets"])
            if assets is None:
                return {"paragraph": paragraph, "is_small_firm": None,
                        "explanation": f"Total assets '{firm['total_assets']}' could not be read: check by hand."}
            is_small = firm["customers"] < customer_limit and assets < asset_limit
            return {
                "paragraph": paragraph,
                "is_small_firm": is_small,
                "explanation": (
                    f"{firm['customers']:,} customers (limit: fewer than {customer_limit:,}) and total assets of "
                    f"{firm['total_assets']} (limit: less than £{match.group(2)} {match.group(3)}), so the firm "
                    f"{'IS' if is_small else 'is NOT'} a small firm under paragraph {paragraph}. "
                    f"{'Small-firm deadlines apply.' if is_small else 'Small-firm deadlines and extensions do NOT apply.'}"
                ),
            }
    return {"paragraph": None, "is_small_firm": None, "explanation": "The document defines no small-firm test."}


def bank_context(firm_id: str) -> dict:
    """Everything recorded about the firm, document-independent: profile, providers, senior managers, important
    business services and the providers they share. Also used by the control tower's "Our bank" view."""
    firm = get_firm_profile(firm_id)
    row = db.get("firms", firm_id=firm_id) or {}
    providers = {p["provider_id"]: p for p in firm["third_party_providers"]}

    senior_managers = [{"role": role, "name": row.get(field)} for field, role in SENIOR_MANAGER_FIELDS.items()
                       if field in row]
    services = [{"name": s["name"], "important": s.get("important", False),
                 "providers": [providers[p]["name"] for p in s.get("provider_ids", []) if p in providers]}
                for s in row.get("business_services", [])]
    important = [s for s in services if s["important"]]
    shared = []
    for provider in firm["third_party_providers"]:
        users = [s["name"] for s in important if provider["name"] in s["providers"]]
        if len(users) > 1:
            shared.append({"provider": provider["name"], "important_services": users,
                           "explanation": f"{provider['name']} supports {len(users)} important business services "
                                          f"({', '.join(users)}): a concentration of risk."})

    gaps = [f"No {m['role']} senior manager is named." for m in senior_managers if not m["name"]]
    gaps += [f"No {field.replace('_', ' ')} is recorded for the firm." for field, value in firm.items()
             if value is None and field not in SENIOR_MANAGER_FIELDS]
    if not services:
        gaps.append("No business services are recorded, so important business services are unknown.")
    return {
        "profile": firm,
        "senior_managers": senior_managers,
        "business_services": services,
        "shared_providers": shared,
        "gaps": gaps,
        "material_providers": [
            {"name": p["name"], "service": p["service"], "contract_end": p["contract_end"]}
            for p in firm["third_party_providers"] if p["material"]
        ],
    }


def get_firm_facts(document_id: str, firm_id: str) -> dict:
    """The firm's facts for this document, for example "PS26-4" and "FIRM-001", worked out in code.

    Returns the profile (with third-party providers, their contract end dates and whether each is material), the
    named senior managers, the important business services and the providers several of them share, the small-firm
    test under the document's own definition, and gaps in the firm's data. Use these facts as given; do not
    recompute them.
    """
    context = bank_context(firm_id)
    return {**context, "small_firm_test": _size_test(document_id, context["profile"])}
