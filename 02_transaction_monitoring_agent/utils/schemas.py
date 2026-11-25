"""The data objects that travel through the 02 graph.

TriageResult comes from code (tools/rules.py). InvestigationReport comes from the investigator model, AuditResult
from the auditor model plus a code check on every cited fact. All three travel inside the decision record, so the
supervisor, the portal and the evals read one place.
"""

from typing import Literal

from pydantic import BaseModel, Field

NextStep = Literal["close_explained", "request_information", "block_and_escalate", "refer_mlro"]
ESCALATING = ("request_information", "block_and_escalate", "refer_mlro")


class RuleFired(BaseModel):
    id: str
    name: str
    weight: int
    paragraph: str
    detail: str


class TriageResult(BaseModel):
    transaction_id: str
    score: int = Field(ge=0, le=100)
    band: Literal["low", "medium", "high"]
    cut_offs: list[int]
    sample: bool
    rules_fired: list[RuleFired]
    rule_set: str
    client_band: Literal["standard", "high_risk"]


class Evidence(BaseModel):
    claim: str = Field(description="One fact in plain English, e.g. 'Card used in person in Lisbon 25 minutes earlier'")
    source: str = Field(description="Where the fact comes from, exactly as '<record>.<field>': a transaction ID "
                                    "such as 'TX-2003.country', or 'profile.<field>' such as "
                                    "'profile.usual_amount_range'")
    value: str = Field(description="The value of that field, copied exactly as it appears in the record")


class InvestigationReport(BaseModel):
    transaction_id: str
    summary: str = Field(description="Two or three sentences a reviewer can read in ten seconds")
    evidence: list[Evidence] = Field(description="Every fact the summary relies on, each with its source field")
    assessment: Literal["likely_legitimate", "unclear", "likely_fraud"]
    next_step: NextStep = Field(description="close_explained, request_information, block_and_escalate, or "
                                            "refer_mlro (the MLRO considers a SAR, para 7.1)")
    paragraphs: list[str] = Field(description="PS26-7 paragraph numbers the report relies on, e.g. ['6.1', '6.2']")
    risk_score: int = Field(ge=0, le=100, description="Your view of the fraud risk, 0 to 100")
    confidence: float = Field(ge=0, le=1, description="How sure you are, 0 to 1")


class AuditResult(BaseModel):
    verdict: Literal["pass", "return"]
    facts_failed: list[str] = Field(description="Evidence whose value does not match its source record")
    issues: list[str] = Field(description="Problems with the reasoning, e.g. 'cites 6.4, but no high-risk merchant "
                                          "rule fired'")
    tipping_off: bool = Field(description="True if any wording a client could see discloses suspicion (para 7.2)")
