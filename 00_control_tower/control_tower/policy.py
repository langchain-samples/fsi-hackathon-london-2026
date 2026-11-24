"""The policy that connects regulation to controls.

The regulatory pillar reads a document like PS26-7 and the supervisor turns its obligations into `PolicyParams`:
numbers and switches that the KYC and TM pillars read from the shared store. Every parameter keeps the paragraph it
came from, so a KYC reason or a TM threshold can always be traced back to the text.

`BASELINE` is what the repo's agents do out of the box (before PS26-7). `PS26_7_REFERENCE` is the hand-checked
answer for PS26-7, used as the gold label in the policy-extraction eval.
"""
from __future__ import annotations

from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field


class PolicyParams(BaseModel):
    """Machine-readable controls derived from a regulation. Fields left at their defaults mean 'no rule'."""

    source_document: str = Field(default="baseline", description="Document ID the policy came from, e.g. 'PS26-7'")

    # KYC / enhanced due diligence
    edd_for_peps: bool = Field(default=True, description="EDD for customers who are PEPs")
    edd_for_pep_family_and_associates: bool = Field(
        default=False, description="EDD for family members and close associates of PEPs, including ones the "
        "customer states in their application")
    high_risk_countries: list[str] = Field(
        default_factory=list, description="Countries whose nationals or residents need EDD, e.g. ['Valdoria']")
    source_of_funds_threshold_gbp: float | None = Field(
        default=None, description="Initial deposit above which source-of-funds evidence is needed, in GBP")

    # Transaction monitoring for high-risk customers
    high_risk_confidence_threshold: float = Field(
        default=0.7, description="For high-risk customers, automated decisions below this confidence go to a human")
    high_risk_review_score: int = Field(
        default=30, description="For high-risk customers, risk scores at or above this go to a human")
    high_risk_merchant_categories: list[str] = Field(
        default_factory=list, description="Merchant categories treated as high risk, e.g. ['crypto', 'gift_cards']")
    high_risk_merchant_limit_gbp: float | None = Field(
        default=None, description="Payments to high-risk merchants above this go to a human, in GBP")

    # Feedback loop from TM to KYC
    alerts_for_kyc_refresh: int | None = Field(
        default=None, description="Number of alerts in the window that triggers a KYC re-rating")
    alert_window_days: int = Field(default=30, description="The window for counting alerts, in days")

    citations: dict[str, str] = Field(
        default_factory=dict, description="Paragraph number each non-default field comes from, keyed by field "
        "name, e.g. {'edd_for_pep_family_and_associates': '4.1'}")


BASELINE = PolicyParams()

PS26_7_REFERENCE = PolicyParams(
    source_document="PS26-7",
    edd_for_peps=True,
    edd_for_pep_family_and_associates=True,
    high_risk_countries=["Valdoria", "Karvonia", "Ostmark"],
    source_of_funds_threshold_gbp=100_000,
    high_risk_confidence_threshold=0.85,
    high_risk_review_score=20,
    high_risk_merchant_categories=["crypto", "gift_cards", "gambling"],
    high_risk_merchant_limit_gbp=250,
    alerts_for_kyc_refresh=2,
    alert_window_days=30,
    citations={
        "edd_for_peps": "4.1",
        "edd_for_pep_family_and_associates": "4.1, 4.3",
        "high_risk_countries": "3.3, 4.2",
        "source_of_funds_threshold_gbp": "4.4",
        "high_risk_confidence_threshold": "6.3",
        "high_risk_review_score": "6.3",
        "high_risk_merchant_categories": "6.4",
        "high_risk_merchant_limit_gbp": "6.4",
        "alerts_for_kyc_refresh": "6.5",
        "alert_window_days": "6.5",
    },
)

EXTRACTION_PROMPT = """You turn a regulatory brief into machine-readable controls for a bank's KYC and
transaction-monitoring agents. Fill in every field the brief supports, and cite the paragraph for each one in
`citations`. Leave a field at its default when the brief does not mention it; never guess.
Merchant categories must use these codes where they apply: crypto, gift_cards, gambling, jewellery, luxury_goods.

Brief for {document_id}:
{brief}"""


def extract_policy(brief: str, document_id: str, model: str) -> PolicyParams:
    """Ask the model to turn the regulatory pillar's brief (or the raw document) into PolicyParams."""
    extractor = init_chat_model(model).with_structured_output(PolicyParams, method="json_schema")
    params = extractor.invoke(EXTRACTION_PROMPT.format(document_id=document_id, brief=brief))
    return params.model_copy(update={"source_document": document_id})


def diff(old: PolicyParams, new: PolicyParams) -> list[dict]:
    """What changed between two policies, for the portal and the audit log."""
    changes = []
    for name in PolicyParams.model_fields:
        if name in ("citations", "source_document"):
            continue
        before, after = getattr(old, name), getattr(new, name)
        if before != after:
            changes.append({"field": name, "before": before, "after": after,
                            "paragraph": new.citations.get(name, "")})
    return changes
