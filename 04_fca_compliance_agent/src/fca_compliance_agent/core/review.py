"""Code that checks the `fca_compliance_reviewer` agent's work. The agent never marks its own work."""

from __future__ import annotations

from typing import Any

from ..domain.models import (
    AnalysisUnit,
    CitationCheck,
    Finding,
    RegulatoryCitation,
    RegulatoryPassage,
    UnitReview,
)
from ..domain.state import UnitAnalysisState
from ..ports.regulatory import FCAHandbookResearchPort
from ..ports.reviewer import ComplianceReviewerPort

SAMPLE_NOTE = "WARNING: this review used sample or mock rules, not real FCA Handbook text."
_TRUNCATED = "[truncated]"
_MIN_QUOTE_WORDS = 4


def _normalise(text: str) -> str:
    straight = text.translate(str.maketrans("\u2018\u2019\u201c\u201d\u2013\u2014\u00a0", "''\"\"-- "))
    return " ".join(straight.split())


def _same_reference(a: str, b: str) -> bool:
    return "".join(a.upper().split()) == "".join(b.upper().split())


def is_sample(passage: RegulatoryPassage) -> bool:
    return passage.metadata.get("source") == "fictional-sample" or passage.metadata.get("corpus") == "mock"


def check_against_passages(
    reference: str, quote: str, passages: list[RegulatoryPassage]
) -> CitationCheck:
    """Check a quote against text that was actually retrieved in this review."""
    matches = [p for p in passages if _same_reference(p.reference, reference)]
    if not matches:
        return CitationCheck(ok=False, problems=[f"{reference} was cited but never read in this review"])
    quote = _normalise(quote)
    if len(quote.split()) < _MIN_QUOTE_WORDS:
        return CitationCheck(ok=False, problems=[f"the quote from {reference} is too short to check"])
    if not any(quote in _normalise(p.text) for p in matches):
        return CitationCheck(ok=False, problems=[f"the quote is not word for word in {reference}"])
    return CitationCheck(ok=True, fictional=any(is_sample(p) for p in matches))


async def _check_fca(
    citation: RegulatoryCitation, review: UnitReview, fca_port: FCAHandbookResearchPort
) -> CitationCheck:
    read = [p for p in review.sections_read if p.source == "fca_handbook"]
    if not any(_same_reference(p.reference, citation.reference) for p in read):
        return CitationCheck(
            ok=False, problems=[f"{citation.reference} was cited without reading its section"]
        )
    try:
        return await fca_port.check_citation(citation.reference, citation.excerpt)
    except Exception as error:  # any failure means unverified, never verified
        return CitationCheck(ok=False, problems=[f"{citation.reference} could not be checked: {error}"])


async def check_finding(
    finding: Finding, review: UnitReview, fca_port: FCAHandbookResearchPort
) -> tuple[Finding, bool]:
    """Keep only verified citations. A finding without a verified binding source needs a person."""
    problems: list[str] = []
    fca: list[RegulatoryCitation] = []
    cca: list[RegulatoryCitation] = []
    binding = fictional = False
    for citation in finding.fca_citations:
        check = await _check_fca(citation, review, fca_port)
        problems += check.problems
        if check.ok:
            fca.append(citation)
            binding = binding or check.binding is True
            fictional = fictional or check.fictional
    read_cca = [p for p in review.sections_read if p.source == "consumer_credit_act"]
    for citation in finding.cca_citations:
        check = check_against_passages(citation.reference, citation.excerpt, read_cca)
        problems += check.problems
        if check.ok:
            cca.append(citation)
            binding = True
            fictional = fictional or check.fictional

    if not fca and not cca:
        problems.append("no citation could be verified")
    elif not binding and finding.status in ("non_compliant", "potential_breach"):
        problems.append("only guidance is cited, and guidance alone cannot be breached")

    update: dict[str, Any] = {"fca_citations": fca, "cca_citations": cca}
    if problems:
        update["status"] = "needs_review"
        update["description"] = (
            f"{finding.description}\n\nNeeds review: {'; '.join(dict.fromkeys(problems))}."
        )
    return finding.model_copy(update=update), fictional


def needs_review_finding(unit: AnalysisUnit, reasons: list[str]) -> Finding:
    """Makes an incomplete review visible. Its status stops the verdict from being compliant."""
    return Finding(
        unit_id=unit.unit_id,
        location=unit.location,
        title=f"Not fully reviewed: {unit.title}",
        description=f"The automated review of this change is incomplete: {'; '.join(reasons)}.",
        severity="medium",
        status="needs_review",
        remediation_guidance="A person should review this change against the FCA Handbook and the CCA.",
        confidence=1.0,
    )


def incomplete_reasons(unit: AnalysisUnit, review: UnitReview) -> list[str]:
    reasons = list(review.problems)
    if review.decision == "needs_review":
        reasons.append(review.rationale or "the agent asked for a person to review it")
    if review.decision == "issues_found" and not review.findings:
        reasons.append("the agent reported issues but listed none")
    if review.decision == "no_issue" and not review.sections_read:
        reasons.append("the agent found no issue without reading any rule")
    if unit.content.rstrip().endswith(_TRUNCATED):
        reasons.append("the change was too long, so only part of it was reviewed")
    return list(dict.fromkeys(reasons))


def make_review_unit_node(
    *, reviewer_port: ComplianceReviewerPort, fca_port: FCAHandbookResearchPort
):
    """Create the `analyze_unit` node that runs the agent on one unit, then checks its work in code."""

    async def analyze_unit(state: UnitAnalysisState) -> dict[str, Any]:
        unit = state["unit"]
        review = await reviewer_port.review_unit(
            unit=unit,
            analysis_input_summary=state["analysis_input_summary"],
            triage=state["triage"],
            regulatory_scope=state.get("regulatory_scope") or [],
        )
        fictional = any(is_sample(p) for p in review.sections_read)
        findings: list[Finding] = []
        for draft in review.findings:
            checked, sample = await check_finding(draft, review, fca_port)
            findings.append(checked)
            fictional = fictional or sample
        if reasons := incomplete_reasons(unit, review):
            findings.append(needs_review_finding(unit, reasons))
        return {
            "unit_findings": findings,
            "queries_executed": review.tool_calls,
            "review_notes": [SAMPLE_NOTE] if fictional else [],
        }

    return analyze_unit
