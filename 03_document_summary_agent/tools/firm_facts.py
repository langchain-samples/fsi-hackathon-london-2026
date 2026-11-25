"""The firm's facts for one document, with the arithmetic done in code rather than by the model.

`get_firm_facts` joins the firm's profile (`get_firm_profile`) with what can be worked out exactly:

- whether the firm is a "small firm" under the document's own size test (both PS26-4 and PS26-7 define one in 2.3),
  which decides whether the small-firm deadlines in the transitional arrangements apply;
- gaps: profile fields that are empty, such as no named senior manager, which become actions in the brief.

The model then only has to apply these facts, never to compare numbers or invent missing data.
"""
import re

from .documents import get_document
from .firms import get_firm_profile

SIZE_TEST = re.compile(
    r"fewer than ([\d,]+) customers and total assets of less than £([\d.]+) (billion|million)", re.IGNORECASE)


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


def get_firm_facts(document_id: str, firm_id: str) -> dict:
    """The firm's profile plus facts worked out in code for this document, for example "PS26-4" and "FIRM-001".

    Returns the profile (with third-party providers, their contract end dates and whether each is material), the
    small-firm test under the document's own definition, and gaps: profile fields that are empty. Use these facts
    as given; do not recompute them.
    """
    firm = get_firm_profile(firm_id)
    gaps = [f"No {field.replace('_', ' ')} is recorded for the firm."
            for field, value in firm.items() if value is None]
    return {
        "profile": firm,
        "small_firm_test": _size_test(document_id, firm),
        "gaps": gaps,
        "material_providers": [
            {"name": p["name"], "service": p["service"], "contract_end": p["contract_end"]}
            for p in firm["third_party_providers"] if p["material"]
        ],
    }
