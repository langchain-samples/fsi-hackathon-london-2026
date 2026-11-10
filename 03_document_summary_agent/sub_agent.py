"""The section-summariser sub-agent: summarises one section of a document and extracts its obligations.

`sub_agent_instructions.md` is its system prompt, and `SectionSummary` is the structured output it must return.
"""
from deepagents import SubAgent
from pydantic import BaseModel, Field
from utils.models import PROJECT_ROOT


# Output schema, returned by each section sub-agent
class Obligation(BaseModel):
    """Something a firm must do under the document."""

    requirement: str = Field(description="What the firm must do, in plain English")
    paragraph: str = Field(description="The paragraph number it comes from, for example '4.2'")
    deadline: str | None = Field(description="The deadline exactly as written in the document, or null if none")
    evidence: str = Field(description="A quote copied word for word from that paragraph, at most 30 words")


class SectionSummary(BaseModel):
    """A summary of one section of the document."""

    number: str = Field(description="The section number, for example '4'")
    title: str = Field(description="The section title")
    summary: str = Field(description="One to three sentences on what the section says")
    obligations: list[Obligation] = Field(description="Every obligation in the section; empty if there are none")


INSTRUCTIONS = (PROJECT_ROOT / "sub_agent_instructions.md").read_text()

section_summariser_subagent: SubAgent = {
    "name": "section-summariser",
    "description": (
        "Summarises ONE section of a regulatory document and extracts its obligations. Pass the section's number, "
        "title and full text in the task description. Returns a SectionSummary."
    ),
    "system_prompt": INSTRUCTIONS,
    "tools": [],
    "response_format": SectionSummary,
}
