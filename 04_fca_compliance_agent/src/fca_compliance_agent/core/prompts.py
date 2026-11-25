"""Prompt builders for the Core Compliance Analysis Agent.

Pure domain-level formatting functions with zero LLM provider dependencies.
"""

from __future__ import annotations

from ..domain.models import AnalysisInput, AnalysisUnit, Finding, RegulatoryPassage


def build_triage_prompt(analysis_input: AnalysisInput) -> str:
    """Build the triage prompt that selects relevant units and drafts FCA/CCA queries."""
    unit_summaries = "\n\n".join(
        f"--- UNIT {u.unit_id} ({u.content_type}) ---\n"
        f"Title: {u.title}\n"
        f"Location: {u.location.model_dump()}\n"
        f"Excerpt:\n{u.content[:1500]}"
        for u in analysis_input.units
    )
    scope = ", ".join(analysis_input.regulatory_scope) or "FCA Handbook (CONC, PRIN 2A) & Consumer Credit Act 1974"

    return (
        "You are a senior UK Financial Services compliance triage specialist.\n"
        f"Target: {analysis_input.title} ({analysis_input.source_type})\n"
        f"Overview: {analysis_input.summary}\n"
        f"Regulatory Scope: {scope}\n\n"
        "For EACH unit listed below:\n"
        "1. Decide whether it touches UK consumer credit, financial promotions, pre-contract disclosures, "
        "creditworthiness/affordability, APR/pricing, arrears/default notices, cooling-off/withdrawal rights, "
        "Section 75 liability, or FCA Consumer Duty (PRIN 2A).\n"
        "2. Set `is_relevant=false` ONLY for pure noise (e.g. formatting, lockfiles, unrelated assets).\n"
        "3. For relevant units, propose 1-3 concise `fca_queries` (for FCA Handbook RAG), "
        "1-3 `cca_queries` (for Consumer Credit Act 1974 search), and any specific `cca_sections` "
        "(e.g. 's.55', 's.66A', 's.75', 's.87').\n\n"
        f"UNITS:\n{unit_summaries}"
    )


def build_unit_analysis_prompt(
    *,
    unit: AnalysisUnit,
    analysis_input_summary: str,
    fca_passages: list[RegulatoryPassage],
    cca_passages: list[RegulatoryPassage],
) -> str:
    """Build the unit-level FCA Handbook vs Consumer Credit Act analysis prompt."""
    fca_block = (
        "\n\n".join(
            f"[{p.reference}] {p.title}\n{p.text}" + (f"\nURL: {p.url}" if p.url else "")
            for p in fca_passages
        )
        or "(No FCA Handbook passages returned)"
    )
    cca_block = (
        "\n\n".join(
            f"[{p.reference}] {p.title}\n{p.text}" + (f"\nURL: {p.url}" if p.url else "")
            for p in cca_passages
        )
        or "(No Consumer Credit Act passages returned)"
    )

    return (
        "You are a UK financial services regulatory compliance analyst specializing in the "
        "FCA Handbook (including CONC and Consumer Duty PRIN 2A) and the Consumer Credit Act 1974 (CCA).\n\n"
        f"Artefact Context:\n{analysis_input_summary}\n\n"
        f"Unit Under Review ({unit.unit_id} — {unit.title}, type={unit.content_type}):\n"
        f"Location: {unit.location.model_dump()}\n"
        f"Content:\n{unit.content}\n\n"
        f"=== RETRIEVED FCA HANDBOOK PASSAGES ===\n{fca_block}\n\n"
        f"=== RETRIEVED CONSUMER CREDIT ACT 1974 PASSAGES ===\n{cca_block}\n\n"
        "Instructions:\n"
        "1. Identify concrete compliance gaps, breaches, missing disclosures, or high-risk omissions.\n"
        "2. Compare FCA Handbook rules against Consumer Credit Act statutory duties explicitly in "
        "`fca_vs_cca_comparison` (note overlapping obligations or statutory minimums that cannot be waived).\n"
        "3. Cite ONLY references grounded in the retrieved passages above.\n"
        "4. Provide actionable `remediation_guidance` detailed enough for either a reviewer, a spec author, "
        "or an autonomous coding remediation agent to fix the issue.\n"
        "5. If the unit is compliant and raises no issues, return an empty list of findings."
    )


def build_executive_summary_prompt(
    *,
    analysis_input: AnalysisInput,
    findings: list[Finding],
) -> str:
    """Build the synthesis prompt for the report-level executive summary."""
    if not findings:
        return (
            f"Write a concise 2-sentence compliance summary confirming that '{analysis_input.title}' "
            f"({analysis_input.source_type}) showed no FCA Handbook or Consumer Credit Act issues across "
            f"{len(analysis_input.units)} analysed units."
        )

    bullet_lines = "\n".join(
        f"- [{f.finding_id}] ({f.severity.upper()} / {f.status}) {f.title} | "
        f"FCA: {[c.reference for c in f.fca_citations]} | "
        f"CCA: {[c.reference for c in f.cca_citations]}"
        for f in findings
    )
    return (
        "You are a Chief Compliance Officer summarising an automated FCA Handbook & Consumer Credit Act review.\n"
        f"Artefact: {analysis_input.title} ({analysis_input.source_type})\n"
        f"Total Findings: {len(findings)}\n\n"
        f"Findings:\n{bullet_lines}\n\n"
        "Write a crisp executive summary (under 150 words) highlighting the most critical FCA/CCA risks, "
        "key statutory vs Handbook themes, and immediate remediation priorities. Ground every claim strictly "
        "in the findings listed above."
    )
