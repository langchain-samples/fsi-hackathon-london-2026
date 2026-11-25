"""The brief: the fixed output shape every run of the document summary agent returns.

It follows the team's agent template (Inputs -> Agent -> Output): an overall summary, then one item per obligation,
each with an `id`, a `summary` and a `source`. The control tower reads it as JSON through
`contracts.brief_from_result`, and the supervisor gets the same JSON back when it calls this agent as a sub-agent.
"""
from pydantic import BaseModel, Field


class BriefItem(BaseModel):
    """One obligation the firm must meet."""

    id: int = Field(description="Running number for this item: 1 for the first obligation, then 2, 3 and so on")
    summary: str = Field(
        description="What the firm must do, in plain English. Keep every number, threshold, amount, list and "
        "deadline exactly as the document states it, and spell out anything a cross-reference points to")
    source: str = Field(description="The document and paragraph it comes from, for example 'PS26-7 para 4.3'")


class Brief(BaseModel):
    """A regulatory brief for one firm."""

    document_id: str = Field(description="The document briefed, for example 'PS26-7'")
    firm_id: str | None = Field(description="The firm the brief is for, for example 'FIRM-001', or null if none")
    title: str = Field(description="The document title")
    summary: str = Field(
        description="Two to four sentences: what the document is about, when it takes effect, and what it means "
        "for this firm in particular")
    items: list[BriefItem] = Field(description="Every obligation in the document, one item each, in document order")
