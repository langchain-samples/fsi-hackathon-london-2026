"""Pure domain models for the FCA Handbook & Consumer Credit Act compliance agent.

Hexagonal boundary rule: this module must NEVER import infrastructure libraries
(MCP, LangChain providers, GitHub CLI, Git, PDF parsers, etc.).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

SourceType = Literal["pr_review", "codebase", "product_spec", "custom"]
# What the caller wants at the end. The inbound adapter decides it, and the outbound adapter delivers exactly that.
OutputType = Literal["report", "pr_comment", "remediation_pr", "pdf_report"]
Severity = Literal["critical", "high", "medium", "low", "info"]
FindingStatus = Literal["non_compliant", "potential_breach", "needs_review", "observation"]
ComplianceVerdict = Literal[
    "compliant",
    "compliant_with_observations",
    "non_compliant",
    "requires_manual_review",
]
RegulatorySource = Literal["fca_handbook", "consumer_credit_act", "uk_gdpr"]

SEVERITY_RANK: dict[Severity, int] = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


# ---------------------------------------------------------------------------
# Polymorphic Location Anchors (Discriminated Union)
# ---------------------------------------------------------------------------


class PullRequestDiffLocation(BaseModel):
    """Anchor to a file & hunk inside a GitHub Pull Request diff."""

    kind: Literal["pr_diff"] = "pr_diff"
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    hunk_header: str | None = None
    diff_position: int | None = None


class CodeLocation(BaseModel):
    """Anchor to a file & line range in a repository codebase."""

    kind: Literal["code"] = "code"
    file_path: str
    start_line: int | None = None
    end_line: int | None = None
    symbol_name: str | None = None


class DocumentLocation(BaseModel):
    """Anchor to a page range & section inside a product specification PDF."""

    kind: Literal["document"] = "document"
    document_name: str
    page_start: int | None = None
    page_end: int | None = None
    section_heading: str | None = None


class GenericLocation(BaseModel):
    """Fallback location anchor for future / custom input modules."""

    kind: Literal["generic"] = "generic"
    reference: str


EvidenceLocation = Annotated[
    PullRequestDiffLocation | CodeLocation | DocumentLocation | GenericLocation,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Normalised Input Contract (Inbound Adapter -> Core)
# ---------------------------------------------------------------------------


class AnalysisUnit(BaseModel):
    """A self-contained chunk of an artefact (diff hunk, source file, PDF section)."""

    unit_id: str
    title: str
    content: str
    location: EvidenceLocation
    content_type: Literal["diff", "code", "spec_section", "config", "other"] = "other"
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnalysisInput(BaseModel):
    """Normalised compliance target produced by any Inbound Adapter."""

    target_id: str
    source_type: SourceType
    title: str
    summary: str = ""
    regulatory_scope: list[str] = Field(
        default_factory=lambda: ["CONC", "PRIN 2A", "UK GDPR", "Consumer Credit Act 1974"]
    )
    units: list[AnalysisUnit] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal Triage & Regulatory Retrieval DTOs
# ---------------------------------------------------------------------------


class TriagedUnit(BaseModel):
    """Triage decision & targeted regulatory queries for a single AnalysisUnit."""

    unit_id: str
    is_relevant: bool = True
    rationale: str = ""
    fca_queries: list[str] = Field(default_factory=list)
    cca_queries: list[str] = Field(default_factory=list)
    cca_sections: list[str] = Field(default_factory=list)


class RegulatoryPassage(BaseModel):
    """A retrieved passage from the FCA Handbook or Consumer Credit Act."""

    source: RegulatorySource
    reference: str
    title: str = ""
    text: str
    url: str | None = None
    score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegulatoryCitation(BaseModel):
    """Grounded citation attached to a Finding."""

    source: RegulatorySource
    reference: str
    excerpt: str = ""
    url: str | None = None


class RegulatorySearch(BaseModel):
    """Search candidates, and any requested sourcebook that could not be searched at all."""

    passages: list[RegulatoryPassage] = Field(default_factory=list)
    not_in_index: list[str] = Field(default_factory=list)


class RegulatorySection(BaseModel):
    """A whole FCA Handbook section, or the part of a long one around the provision asked for."""

    section: str
    passages: list[RegulatoryPassage]
    complete: bool = True
    outline: list[str] = Field(default_factory=list, description="Provisions left out of a long section")
    definitions: dict[str, str] = Field(default_factory=dict)
    links: list[str] = Field(default_factory=list, description="References to provisions outside this section")


class CitationCheck(BaseModel):
    """The result of checking a quote against its source, without a model."""

    ok: bool
    problems: list[str] = Field(default_factory=list)
    binding: bool | None = Field(default=None, description="False for guidance; None if not known")
    fictional: bool = Field(default=False, description="True if the source is sample or mock text")


# ---------------------------------------------------------------------------
# Core Output Contract (Core -> HITL Gate -> Outbound Adapter)
# ---------------------------------------------------------------------------


class Finding(BaseModel):
    """A single structured FCA / Consumer Credit Act compliance finding."""

    finding_id: str = Field(default_factory=lambda: f"F-{uuid4().hex[:6].upper()}")
    unit_id: str
    title: str
    description: str
    severity: Severity
    status: FindingStatus = "potential_breach"
    location: EvidenceLocation
    evidence_excerpt: str = ""
    fca_citations: list[RegulatoryCitation] = Field(default_factory=list)
    cca_citations: list[RegulatoryCitation] = Field(default_factory=list)
    fca_vs_cca_comparison: str = Field(
        default="",
        description="Comparison of FCA Handbook rules against Consumer Credit Act statutory obligations.",
    )
    remediation_guidance: str
    suggested_change: str | None = None
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class FindingsReport(BaseModel):
    """Structured compliance findings report emitted by the Core Analysis Subgraph."""

    report_id: str = Field(default_factory=lambda: f"REP-{uuid4().hex[:8].upper()}")
    target_id: str
    source_type: SourceType
    title: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    overall_verdict: ComplianceVerdict
    executive_summary: str
    findings: list[Finding] = Field(default_factory=list)
    units_total: int = 0
    units_triaged_in: int = 0
    units_skipped: int = 0
    regulatory_queries_executed: list[str] = Field(default_factory=list)
    scope_not_searched: list[str] = Field(
        default_factory=list,
        description="Parts of the regulatory scope that the FCA index does not hold, so no rule in them was checked",
    )

    def scope_warning(self) -> str | None:
        if not self.scope_not_searched:
            return None
        return f"**Not checked:** {', '.join(self.scope_not_searched)} (not in the FCA index that was searched)"

    def severity_counts(self) -> dict[Severity, int]:
        counts: dict[Severity, int] = {
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "info": 0,
        }
        for finding in self.findings:
            counts[finding.severity] += 1
        return counts


# ---------------------------------------------------------------------------
# HITL & Outbound Deliverable Contracts
# ---------------------------------------------------------------------------


class HumanReviewDecision(BaseModel):
    """Decision returned when resuming the optional HITL interrupt gate."""

    action: Literal["approve", "filter", "reject"] = "approve"
    approved_finding_ids: list[str] | None = None
    reviewer_notes: str = ""


class DeliverableResult(BaseModel):
    """Result produced by an Outbound Adapter node/subgraph."""

    module_name: str
    status: Literal["completed", "aborted_by_reviewer", "skipped", "failed"]
    output_type: OutputType | None = Field(
        default=None, description="The output that was asked for. `status` and `metadata` say whether it was delivered."
    )
    summary: str
    artifacts: list[str] = Field(
        default_factory=list,
        description="Created URLs, PR links, comment IDs, or output file paths.",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
