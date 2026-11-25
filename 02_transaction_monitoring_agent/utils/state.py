from dataclasses import dataclass
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages

OCT_19 = "2026-10-19"  # Module 3's transactions start here


@dataclass
class MyAgentContext:
    """Information we pass in with every run. Code reads it; the models can't change it.

    `user_id`, `confidence_threshold` and `grey_zone` are the control tower's contract and keep their names.
    `grey_zone` is now the band cut-offs: medium from grey_zone[0], high from grey_zone[1].
    """
    user_id: str = ""

    confidence_threshold: float = 0.7       # investigator below this: the reviewer is told it is unsure
    checked_confidence: float = 0.5
    grey_zone: tuple[int, int] = (30, 70)   # the cut-offs: medium from 30, high from 70

    # Added for the rules engine. The control tower sets these from the KYC record and the live policy.
    kyc_rating: str | None = None           # selects the rules; no rating is treated as medium (standard)
    kyc_reasons: tuple[str, ...] = ()       # why KYC rated the client that way, for the investigator
    rule_set: str = "retail_cards"
    policy_version: str = "baseline"


class TmState(TypedDict, total=False):
    """One conversation per customer and run. `messages` carries the run's short-term memory across transactions;
    every other field describes the transaction being worked on and is reset when the next one arrives."""
    messages: Annotated[list, add_messages]
    transaction_id: str
    transaction: dict
    profile: dict
    earlier: list[dict]
    notes: list[dict]
    recent_alerts: list[dict]
    triage: dict
    report: dict | None
    audit: dict | None
    returns: list[dict]
    investigations: int
    investigator_run: dict | None   # LangSmith run and trace IDs, so the auditor's verdict lands on the right run
    decision: dict | None
