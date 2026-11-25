"""The reviewer summary: the second step, after the obligations have been extracted into a `Brief`.

It follows MillTech's horizon-scanning format: a classification and impact, then sections A to E (summary,
applicability, impact, recommended actions in three tiers, takeaways and open questions), each tied to paragraphs.

Facts are worked out in code (`key_dates`, `counts`, the firm's facts); the model only writes the narrative on top of
them. A person reviews it: for a document that changes KYC or monitoring controls, the control tower shows the
control changes separately for approval.
"""
import json
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from brief import Brief
from tools.firm_facts import get_firm_facts
from utils import PROJECT_ROOT

INSTRUCTIONS = (PROJECT_ROOT / "reviewer_instructions.md").read_text(encoding="utf-8")
DATE = re.compile(r"\b(\d{1,2} (?:January|February|March|April|May|June|July|August|September|October|November|"
                  r"December) \d{4})\b")


# --- written by the model --------------------------------------------------------------------------------------

class ReviewAction(BaseModel):
    """One recommended action for the bank."""

    action: str = Field(description="What to do, in one sentence, naming the bank's own providers, people or gaps")
    owner: str = Field(description="The owning team, exactly as listed in the instructions")
    deadline: str | None = Field(description="The firm deadline, written like '30 June 2027', or null")
    source: str = Field(description="The paragraph(s) it rests on, written like 'PS26-4 para 8.1'")


class OpenQuestion(BaseModel):
    """Something the reviewer needs to settle before the assessment is final."""

    question: str = Field(description="The question, in one sentence")
    owner: str = Field(description="The team that can answer it, exactly as listed in the instructions")
    source: str = Field(description="The paragraph(s) it rests on, written like 'PS26-4 para 6.5'")


class ReviewSummary(BaseModel):
    """The reviewer summary, sections A to E."""

    classification: Literal["Actionable", "Indicative", "Informative"] = Field(
        description="Actionable: creates or changes obligations for the bank. Indicative: may do so in future. "
        "Informative: awareness only")
    impact: Literal["High", "Medium", "Low"] = Field(description="High: near-term deadlines or direct obligation "
                                                     "changes. Medium: likely future impact. Low: awareness")
    impact_reason: str = Field(description="The single strongest reason for the impact rating, in one sentence")
    effective_date: str | None = Field(description="When the document takes effect, written like '1 March 2027'")
    owners: list[str] = Field(description="The teams that own the response, from the list in the instructions")
    a_summary: str = Field(description="A. What was published, by whom, and its substance: 2 to 4 sentences")
    b_applicability: str = Field(description="B. Whether and why it applies to this bank, including the small-firm "
                                 "test: 2 to 4 sentences")
    c_impact: str = Field(description="C. What it changes for the bank and what happens if it does nothing: "
                          "2 to 4 sentences")
    d_immediate: list[ReviewAction] = Field(description="D. Immediate actions: confirm scope, assign owners, "
                                            "anything due first or already a gap")
    d_near_term: list[ReviewAction] = Field(description="D. Near-term actions: policies, procedures, controls, "
                                            "contracts and systems to change")
    d_monitoring: list[ReviewAction] = Field(description="D. Ongoing monitoring: recurring reviews, tests and "
                                             "future publications to track")
    e_takeaways: list[str] = Field(description="E. Three to six key takeaways, one sentence each")
    e_open_questions: list[OpenQuestion] = Field(description="E. Open questions and information gaps, including "
                                                 "every item marked 'unclear' for the bank")


# --- worked out in code ----------------------------------------------------------------------------------------

class KeyDate(BaseModel):
    """A firm deadline and the items that fall due on it."""

    date: str = Field(description="The date, written like '30 June 2027'")
    iso: str = Field(description="The same date as YYYY-MM-DD, for sorting")
    item_ids: list[int] = Field(description="The Brief items due on this date")
    sources: list[str] = Field(description="Their sources")


class BriefCounts(BaseModel):
    """How many obligations apply to the bank."""

    total: int
    applies: int
    check: int
    does_not_apply: int
    with_deadline: int


class ReviewedBrief(Brief):
    """The Brief plus the reviewer summary: what the agent returns."""

    review: ReviewSummary = Field(description="The reviewer summary, sections A to E")
    key_dates: list[KeyDate] = Field(description="Firm deadlines in date order, worked out in code")
    counts: BriefCounts = Field(description="Item counts, worked out in code")


def key_dates(brief: Brief) -> list[KeyDate]:
    """Every dated firm deadline, earliest first, with the items due on it."""
    by_date: dict[str, KeyDate] = {}
    for item in brief.items:
        for text in DATE.findall(item.firm_deadline or ""):
            iso = datetime.strptime(text, "%d %B %Y").date().isoformat()
            entry = by_date.setdefault(iso, KeyDate(date=text, iso=iso, item_ids=[], sources=[]))
            if item.id not in entry.item_ids:
                entry.item_ids.append(item.id)
            if item.source not in entry.sources:
                entry.sources.append(item.source)
    return [by_date[k] for k in sorted(by_date)]


def counts(brief: Brief) -> BriefCounts:
    status = [item.applies_to_firm for item in brief.items]
    return BriefCounts(total=len(brief.items), applies=status.count("yes"), check=status.count("unclear"),
                       does_not_apply=status.count("no"),
                       with_deadline=sum(1 for item in brief.items if item.firm_deadline))


def review_brief(brief: Brief, model) -> ReviewedBrief:
    """Write the reviewer summary for a Brief with `model` (a chat model) and return the ReviewedBrief."""
    dates, tally = key_dates(brief), counts(brief)
    facts = get_firm_facts(brief.document_id, brief.firm_id) if brief.firm_id else None
    prompt = "\n\n".join([
        INSTRUCTIONS,
        f"## The firm's facts (worked out in code; use as given)\n{json.dumps(facts, ensure_ascii=False, indent=1)}",
        f"## Counts (worked out in code)\n{tally.model_dump_json()}",
        f"## Firm deadlines in date order (worked out in code)\n"
        f"{json.dumps([d.model_dump() for d in dates], ensure_ascii=False)}",
        f"## The brief\n{brief.model_dump_json(indent=1)}",
    ])
    summary = model.with_structured_output(ReviewSummary, method="json_schema").invoke(prompt)
    return ReviewedBrief(**brief.model_dump(), review=summary, key_dates=dates, counts=tally)
