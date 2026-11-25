"""Citation grounding: findings may only cite rules that were actually retrieved."""

from __future__ import annotations

import unittest

from fca_compliance_agent import Finding, PullRequestDiffLocation, RegulatoryCitation, RegulatoryPassage
from fca_compliance_agent.core.nodes import _ground_findings

PASSAGES = [
    RegulatoryPassage(source="consumer_credit_act", reference="CCA 1974 s.87", text="Need for default notice", url="u87"),
    RegulatoryPassage(source="fca_handbook", reference="CONC 5.2A.4R", text="Creditworthiness assessment"),
]


def _finding(*refs: tuple[str, str]) -> Finding:
    return Finding(
        unit_id="u1",
        title="t",
        description="d",
        severity="high",
        status="non_compliant",
        location=PullRequestDiffLocation(file_path="a.py"),
        remediation_guidance="fix",
        fca_citations=[RegulatoryCitation(source="fca_handbook", reference=r) for s, r in refs if s == "fca"],
        cca_citations=[RegulatoryCitation(source="consumer_credit_act", reference=r) for s, r in refs if s == "cca"],
    )


class GroundingTest(unittest.TestCase):
    def test_parenthetical_reference_is_matched_and_canonicalised(self) -> None:
        [f] = _ground_findings([_finding(("cca", "CCA 1974 s.87 (and s.88 cure period)"))], PASSAGES)
        self.assertEqual([c.reference for c in f.cca_citations], ["CCA 1974 s.87"])
        self.assertEqual(f.cca_citations[0].url, "u87")
        self.assertEqual(f.status, "non_compliant")

    def test_unretrieved_reference_is_dropped(self) -> None:
        [f] = _ground_findings([_finding(("fca", "CONC 5.2A.4R"), ("fca", "CONC 99.9R"))], PASSAGES)
        self.assertEqual([c.reference for c in f.fca_citations], ["CONC 5.2A.4R"])

    def test_gdpr_points_stay_distinct(self) -> None:
        gdpr = [
            RegulatoryPassage(source="uk_gdpr", reference="UK GDPR Art 5(1)(b)", text="purpose"),
            RegulatoryPassage(source="uk_gdpr", reference="UK GDPR Art 5(1)(c)", text="minimisation"),
        ]
        [f] = _ground_findings([_finding(("fca", "UK GDPR Art 5(1)(c)"))], gdpr)
        self.assertEqual([c.reference for c in f.fca_citations], ["UK GDPR Art 5(1)(c)"])

    def test_gdpr_is_searchable_in_default_scope(self) -> None:
        import asyncio

        from fakes import MockFCAHandbookAdapter
        from fca_compliance_agent import AnalysisInput

        scope = AnalysisInput(target_id="t", source_type="pr_review", title="t").regulatory_scope
        hits = asyncio.run(MockFCAHandbookAdapter().search_rules("data minimisation personal data", sourcebooks=scope))
        self.assertIn("UK GDPR Art 5(1)(c)", [p.reference for p in hits])

    def test_finding_with_no_grounded_citation_is_downgraded(self) -> None:
        [f] = _ground_findings([_finding(("fca", "UK GDPR Art 99"))], PASSAGES)
        self.assertEqual((f.fca_citations, f.status), ([], "needs_review"))
        self.assertLessEqual(f.confidence, 0.4)


if __name__ == "__main__":
    unittest.main()
