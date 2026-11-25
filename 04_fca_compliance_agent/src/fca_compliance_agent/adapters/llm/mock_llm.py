"""Deterministic offline MockComplianceAnalyzer for tests & local demos.

Inspects unit text against the actually retrieved FCA Handbook & Consumer Credit Act
passages so end-to-end tests validate triage, parallel fan-out, regulatory grounding,
and synthesis without making external LLM network calls.
"""

from __future__ import annotations

from ...domain.models import (
    AnalysisInput,
    AnalysisUnit,
    Finding,
    RegulatoryCitation,
    RegulatoryPassage,
    TriagedUnit,
)

_NOISE_MARKERS = (".css", ".svg", "package-lock", "yarn.lock", "favicon", "changelog")

_CREDIT_RULES: list[dict] = [
    {
        "triggers": ("guaranteed approval", "no credit check", "skip_affordability", "skip_creditworthiness"),
        "title": "Missing creditworthiness assessment / misleading 'guaranteed approval' claim",
        "severity": "critical",
        "status": "non_compliant",
        "fca_ref_prefix": "CONC 5.2A",
        "fca_secondary_prefix": "CONC 3.3",
        "cca_ref_prefix": "CCA 1974 s.140A",
        "comparison": (
            "FCA CONC 5.2A.4R mandates a reasonable creditworthiness & affordability check before "
            "lending, while CONC 3.3.1R prohibits misleading 'guaranteed approval' promotions. "
            "Bypassing checks also exposes the lender to unfair-relationship challenges under CCA 1974 s.140A."
        ),
        "remediation": (
            "Enforce a mandatory affordability and creditworthiness check before loan approval, "
            "and remove 'guaranteed approval' language from customer-facing copy."
        ),
        "suggested_change": "require_affordability_check = True  # Enforce CONC 5.2A.4R before approval",
    },
    {
        "triggers": ("withdrawal_days = 7", "7-day cooling", "7 day withdrawal", "no right to withdraw"),
        "title": "Statutory right of withdrawal shorter than 14 calendar days",
        "severity": "critical",
        "status": "non_compliant",
        "fca_ref_prefix": "CONC 4.2",
        "cca_ref_prefix": "CCA 1974 s.66A",
        "comparison": (
            "CCA 1974 s.66A grants a non-waivable statutory 14-calendar-day right of withdrawal; "
            "FCA CONC 4.2.5R independently requires clear pre-contract explanation of this 14-day right."
        ),
        "remediation": (
            "Update the withdrawal window to at least 14 calendar days and ensure pre-contract "
            "SECCI explanations prominently state the s.66A right to withdraw."
        ),
        "suggested_change": "WITHDRAWAL_PERIOD_DAYS = 14  # Statutory minimum under CCA 1974 s.66A",
    },
    {
        "triggers": ("default_notice_days = 5", "immediate termination", "7 days to cure", "skip_default_notice"),
        "title": "Default notice cure period below statutory 14-day minimum",
        "severity": "high",
        "status": "non_compliant",
        "fca_ref_prefix": "CONC 7.3",
        "cca_ref_prefix": "CCA 1974 s.87",
        "comparison": (
            "CCA 1974 s.87/s.88 strictly requires a prescribed Default Notice giving not less than "
            "14 days to remedy before termination or enforcement, reinforcing FCA CONC 7.3.2G forbearance."
        ),
        "remediation": (
            "Emit a prescribed CCA s.87 Default Notice with at least a 14-day cure period before "
            "accelerating debt or terminating the credit agreement."
        ),
        "suggested_change": "DEFAULT_NOTICE_CURE_DAYS = 14  # Minimum statutory cure period (CCA s.87/s.88)",
    },
    {
        "triggers": ("waive section 75", "exclude s.75", "no creditor liability"),
        "title": "Attempted exclusion of Consumer Credit Act Section 75 joint liability",
        "severity": "high",
        "status": "non_compliant",
        "fca_ref_prefix": "PRIN 2A",
        "cca_ref_prefix": "CCA 1974 s.75",
        "comparison": (
            "CCA 1974 s.75 imposes statutory joint and several liability for financed purchases "
            "between £100 and £30,000 (cannot be contracted out); attempting to waive it also "
            "breaches FCA Consumer Duty (PRIN 2A.2.1R)."
        ),
        "remediation": (
            "Remove any clause excluding creditor liability for supplier breach/misrepresentation "
            "and surface clear Section 75 consumer protection wording."
        ),
        "suggested_change": "Remove waiver clause; preserve statutory CCA 1974 s.75 consumer rights.",
    },
    {
        "triggers": ("hide_apr", "apr_footer_only", "omit representative apr", "headline rate only"),
        "title": "Representative APR omitted or insufficiently prominent in credit promotion",
        "severity": "medium",
        "status": "potential_breach",
        "fca_ref_prefix": "CONC 3.5",
        "cca_ref_prefix": "CCA 1974 s.55",
        "comparison": (
            "FCA CONC 3.5.7R mandates a prominent Representative APR alongside rate/cost triggers, "
            "complementing pre-contract cost transparency duties under CCA 1974 s.55."
        ),
        "remediation": (
            "Display the Representative APR with equal or greater prominence than headline rates "
            "and deliver full SECCI pre-contract disclosures prior to execution."
        ),
        "suggested_change": "show_representative_apr_prominently = True  # CONC 3.5.7R",
    },
]


def _pick_citations(
    passages: list[RegulatoryPassage],
    prefixes: tuple[str, ...],
) -> list[RegulatoryCitation]:
    matched = [
        RegulatoryCitation(
            source=p.source,
            reference=p.reference,
            excerpt=p.text[:220],
            url=p.url,
        )
        for p in passages
        if any(p.reference.upper().startswith(pref.upper()) for pref in prefixes)
    ]
    if matched:
        return matched
    if passages:
        first = passages[0]
        return [
            RegulatoryCitation(
                source=first.source,
                reference=first.reference,
                excerpt=first.text[:220],
                url=first.url,
            )
        ]
    return []


class MockComplianceAnalyzer:
    """Deterministic offline implementation of `ComplianceAnalyzerPort`."""

    async def triage_units(self, analysis_input: AnalysisInput) -> list[TriagedUnit]:
        decisions: list[TriagedUnit] = []
        for unit in analysis_input.units:
            combined_meta = f"{unit.title} {unit.location.model_dump()}".lower()
            if any(marker in combined_meta for marker in _NOISE_MARKERS):
                decisions.append(
                    TriagedUnit(
                        unit_id=unit.unit_id,
                        is_relevant=False,
                        rationale="Non-regulatory static/asset file skipped during triage.",
                    )
                )
                continue

            text_lower = unit.content.lower()
            fca_queries: list[str] = []
            cca_queries: list[str] = []
            cca_sections: list[str] = []

            if any(k in text_lower for k in ("affordability", "creditworthiness", "guaranteed approval", "credit check")):
                fca_queries.append("creditworthiness affordability assessment guaranteed approval CONC")
                cca_queries.append("unfair relationships creditworthiness CCA")
                cca_sections.append("s.140A")
            if any(k in text_lower for k in ("withdraw", "cooling", "pre-contract", "secci")):
                fca_queries.append("adequate explanations right to withdraw 14 days CONC 4.2")
                cca_queries.append("statutory right of withdrawal 14 days pre-contract")
                cca_sections.extend(["s.55", "s.66A"])
            if any(k in text_lower for k in ("default", "arrears", "termination", "cure")):
                fca_queries.append("forbearance default notice arrears CONC 7.3")
                cca_queries.append("default notice 14 days termination enforcement")
                cca_sections.append("s.87")
            if any(k in text_lower for k in ("section 75", "s.75", "creditor liability")):
                fca_queries.append("consumer duty good outcomes foreseeable harm PRIN 2A")
                cca_queries.append("joint and several liability supplier breach section 75")
                cca_sections.append("s.75")
            if any(k in text_lower for k in ("apr", "interest", "promotion", "headline rate")):
                fca_queries.append("representative APR prominence financial promotion CONC 3.5")
                cca_queries.append("pre-contract credit information disclosure")
                cca_sections.append("s.55")

            if not fca_queries:
                fca_queries = [f"{unit.title} consumer credit FCA CONC Consumer Duty"]
            if not cca_queries:
                cca_queries = [f"{unit.title} Consumer Credit Act 1974 obligations"]

            decisions.append(
                TriagedUnit(
                    unit_id=unit.unit_id,
                    is_relevant=True,
                    rationale="Touches consumer credit lifecycle, disclosures, pricing, or enforcement.",
                    fca_queries=fca_queries,
                    cca_queries=cca_queries,
                    cca_sections=list(dict.fromkeys(cca_sections)),
                )
            )
        return decisions

    async def analyze_unit(
        self,
        *,
        unit: AnalysisUnit,
        analysis_input_summary: str,
        fca_passages: list[RegulatoryPassage],
        cca_passages: list[RegulatoryPassage],
    ) -> list[Finding]:
        text_lower = unit.content.lower()
        findings: list[Finding] = []

        for rule in _CREDIT_RULES:
            matched_trigger = next((t for t in rule["triggers"] if t in text_lower), None)
            if not matched_trigger:
                continue

            fca_prefixes = (
                (rule["fca_ref_prefix"], rule["fca_secondary_prefix"])
                if "fca_secondary_prefix" in rule
                else (rule["fca_ref_prefix"],)
            )
            fca_citations = _pick_citations(fca_passages, fca_prefixes)
            cca_citations = _pick_citations(cca_passages, (rule["cca_ref_prefix"],))

            findings.append(
                Finding(
                    unit_id=unit.unit_id,
                    title=rule["title"],
                    description=(
                        f"Detected '{matched_trigger}' in {unit.title} ({analysis_input_summary[:80]}). "
                        f"{rule['comparison']}"
                    ),
                    severity=rule["severity"],
                    status=rule["status"],
                    location=unit.location,
                    evidence_excerpt=unit.content.strip()[:280],
                    fca_citations=fca_citations,
                    cca_citations=cca_citations,
                    fca_vs_cca_comparison=rule["comparison"],
                    remediation_guidance=rule["remediation"],
                    suggested_change=rule["suggested_change"],
                    confidence=0.92,
                )
            )
        return findings

    async def synthesize_executive_summary(
        self,
        *,
        analysis_input: AnalysisInput,
        findings: list[Finding],
    ) -> str:
        if not findings:
            return (
                f"Automated FCA Handbook & Consumer Credit Act review of '{analysis_input.title}' "
                f"({analysis_input.source_type}) found no compliance issues across "
                f"{len(analysis_input.units)} unit(s)."
            )
        severities = ", ".join(
            f"{sev}: {sum(1 for f in findings if f.severity == sev)}"
            for sev in ("critical", "high", "medium", "low", "info")
            if any(f.severity == sev for f in findings)
        )
        top_titles = "; ".join(f.title for f in findings[:3])
        return (
            f"Identified {len(findings)} FCA/CCA compliance finding(s) in '{analysis_input.title}' "
            f"({severities}). Priority issues: {top_titles}."
        )
