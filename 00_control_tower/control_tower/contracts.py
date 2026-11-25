"""What the supervisor needs from each agent: the contract between the Control Tower and the three pillars.

Each pillar owner can change their agent however they like, as long as these still hold. Check with:

    uv run pytest -q 00_control_tower/tests/test_contracts.py      (from the repo root)

The adapters in `pillars/` are written to accept the changes we expect on the day (for example KYC adding a "medium"
rating or giving reasons with paragraph numbers, or the regulatory agent returning a structured brief), so most edits
need no change here.

How a pillar reads the live policy (optional): the adapters compile each agent with the shared store, so a LangGraph
node or tool can call `read_policy(runtime.store)` and get the PS26-7 parameters as a dict (or None before any policy
is approved). See docs/process_maps for which parameters matter to which pillar.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Level = Literal["low", "medium", "high"]


# --- 01 KYC (Nam) --------------------------------------------------------------------------------------------

class Reason(BaseModel):
    level: Level = "low"
    text: str
    paragraph: str = Field(default="", description="The regulation paragraph this reason relies on, if any")


class KycProfile(BaseModel, extra="allow"):
    full_name: str
    nationality: str = ""


class KycOutput(BaseModel, extra="allow"):
    """The KYC graph's final state, as the supervisor reads it."""

    application: str = Field(description="The raw application text")
    profile: KycProfile
    risk_level: Level
    risk_reasons: list[str | Reason] = Field(default_factory=list)
    decision: Literal["approve", "reject"] | None = Field(
        default=None, description="None while the graph is paused for a human")

    @field_validator("risk_level", mode="before")
    @classmethod
    def _lower(cls, v):
        return v.lower() if isinstance(v, str) else v


def kyc_reasons(state: KycOutput) -> list[dict]:
    """Normalise the KYC graph's reasons to [{level, text, paragraph}], dropping 'no match' filler."""
    out = []
    for r in state.risk_reasons:
        reason = r if isinstance(r, Reason) else Reason(level=state.risk_level, text=r)
        if reason.text.lower().startswith("no watchlist match"):
            continue
        out.append(reason.model_dump())
    return out


# --- 02 Transaction monitoring (Marcus) ----------------------------------------------------------------------

class TmContextFields(BaseModel):
    """Fields the supervisor sets on the TM agent's context for each run."""

    user_id: str
    confidence_threshold: float = Field(ge=0, le=1)
    grey_zone: tuple[int, int]


class TmDecision(BaseModel, extra="allow"):
    """One record in the ("decisions", user_id) store namespace, written by the TM agent's record_decision.

    `decided_by` is "rules" for low-band payments the rules engine cleared without a model or a person, "human" for
    every alert. "agent" is kept for older 02 versions. Extra fields (band, rules_fired, next_step, audit, report,
    kyc_rating, rule_set, policy_version...) travel with the record without a contract change.
    """

    transaction_id: str
    risk_score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    final_decision: Literal["approve", "block"]
    decided_by: Literal["agent", "human", "rules"]
    reason: str = ""


# --- 03 Regulatory change (Alejandro) ------------------------------------------------------------------------

def reg_request(document_id: str, firm_id: str) -> dict:
    """The message the supervisor sends the regulatory agent."""
    return {"messages": [{"role": "user", "content": f"Summarise document {document_id} for {firm_id}"}]}


def brief_from_result(result: dict) -> str:
    """The brief, whatever shape the regulatory agent returns it in.

    Today it is the final message's text. If the agent is given a `response_format` (for example a Sections A-E
    brief), its structured response is used instead, as JSON.
    """
    structured = result.get("structured_response")
    if structured is not None:
        data = structured.model_dump() if hasattr(structured, "model_dump") else structured
        return json.dumps(data, indent=2, ensure_ascii=False, default=str)
    message = result["messages"][-1]
    text = getattr(message, "text", None)
    if callable(text) and not isinstance(text, str):
        text = text()
    return text if text is not None else str(getattr(message, "content", message))


# --- Shared policy -------------------------------------------------------------------------------------------

def read_policy(store: Any, firm_id: str = "FIRM-001") -> dict | None:
    """The live policy for a firm from the shared store, for any pillar to read."""
    if store is None:
        return None
    item = store.get(("policy", firm_id), "active")
    return item.value if item else None
