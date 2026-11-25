"""The brief: the fixed output shape every run of the document summary agent returns.

It follows the team's agent template (Inputs -> Agent -> Output): an overall summary, then one item per obligation.
Every item has the template's three fields (`id`, `summary`, `source`) plus three firm fields that say what the
obligation means for the firm the brief is for (`applies_to_firm`, `firm_deadline`, `firm_action`).

The control tower reads it as JSON through `contracts.brief_from_result`, and the supervisor gets the same JSON back
when it calls this agent as a sub-agent.
"""
from typing import Literal

from pydantic import BaseModel, Field


class BriefItem(BaseModel):
    """One obligation, and what it means for the firm."""

    id: int = Field(description="Running number for this item: 1 for the first obligation, then 2, 3 and so on")
    summary: str = Field(
        description="What firms must do, in plain English. Keep every number, threshold, amount, list and deadline "
        "exactly as the document states it, and spell out anything a cross-reference points to")
    source: str = Field(description="The document and paragraph it comes from, for example 'PS26-7 para 4.3'")
    applies_to_firm: Literal["yes", "no", "unclear"] | None = Field(
        description="Whether the obligation applies to this firm: 'no' only when the firm's facts rule it out (for "
        "example a small-firm extension for a firm that is not small); 'unclear' when the firm data does not say; "
        "null when no firm was given")
    firm_deadline: str | None = Field(
        description="The date by which this firm must meet it, after applying the transitional arrangements and the "
        "firm's own dates (for example a contract end date), written like '30 June 2027'; null if there is none")
    firm_action: str | None = Field(
        description="What this firm specifically must do, naming its providers, contracts or people, and any gap in "
        "its data (for example 'No senior manager is named: appoint and notify one'); null if no firm was given or "
        "the obligation does not apply")


class Brief(BaseModel):
    """A regulatory brief for one firm."""

    document_id: str = Field(description="The document briefed, for example 'PS26-7'")
    firm_id: str | None = Field(description="The firm the brief is for, for example 'FIRM-001', or null if none")
    title: str = Field(description="The document title")
    summary: str = Field(
        description="Two to four sentences: what the document is about, when it takes effect, and what it means "
        "for this firm in particular, including whether it is a small firm")
    items: list[BriefItem] = Field(description="Every obligation in the document, one item each, in document order")
