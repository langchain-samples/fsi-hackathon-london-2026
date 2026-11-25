"""In-memory mock adapters for the FCA Handbook RAG & Consumer Credit Act 1974.

Used for offline development, demos, and automated testing while the external
MCP servers are being built by the partner team.
"""

from __future__ import annotations

import re

from ...domain.models import (
    CitationCheck,
    RegulatoryPassage,
    RegulatorySearch,
    RegulatorySection,
)

_FCA_CORPUS: list[RegulatoryPassage] = [
    RegulatoryPassage(
        source="fca_handbook",
        reference="CONC 3.3.1R",
        title="Financial promotions must be clear, fair and not misleading",
        text=(
            "A firm must ensure that a communication or a financial promotion is clear, fair and "
            "not misleading. Claims such as 'guaranteed approval', 'instant approval without checks', "
            "or omitting key eligibility conditions breach CONC 3.3.1R."
        ),
        url="https://www.handbook.fca.org.uk/handbook/CONC/3/3.html",
    ),
    RegulatoryPassage(
        source="fca_handbook",
        reference="CONC 3.5.7R",
        title="Prominence of Representative APR in credit promotions",
        text=(
            "Where a financial promotion indicates a rate of interest or amount relating to the cost "
            "of credit, it must include a representative example with the Representative APR shown "
            "with no less prominence than any other rate or incentive."
        ),
        url="https://www.handbook.fca.org.uk/handbook/CONC/3/5.html",
    ),
    RegulatoryPassage(
        source="fca_handbook",
        reference="CONC 4.2.5R",
        title="Adequate explanations before a regulated credit agreement is made",
        text=(
            "Before making a regulated credit agreement, the lender must provide an adequate "
            "explanation covering key features, total amount payable, consequences of missed "
            "payments, and the customer's statutory right to withdraw within 14 days."
        ),
        url="https://www.handbook.fca.org.uk/handbook/CONC/4/2.html",
    ),
    RegulatoryPassage(
        source="fca_handbook",
        reference="CONC 5.2A.4R",
        title="Creditworthiness and affordability assessment",
        text=(
            "A firm must undertake a reasonable assessment of creditworthiness before entering into "
            "a regulated credit agreement or significantly increasing the credit limit, considering "
            "both credit risk and affordability without undue hardship."
        ),
        url="https://www.handbook.fca.org.uk/handbook/CONC/5/2A.html",
    ),
    RegulatoryPassage(
        source="fca_handbook",
        reference="CONC 7.3.2G",
        title="Treating customers in default or arrears difficulties with forbearance",
        text=(
            "A firm must treat customers in default or arrears difficulties with forbearance and "
            "due consideration, and must not accelerate or enforce a regulated credit agreement "
            "without first issuing the statutory default notice and respecting the cure period."
        ),
        url="https://www.handbook.fca.org.uk/handbook/CONC/7/3.html",
    ),
    RegulatoryPassage(
        source="fca_handbook",
        reference="PRIN 2A.2.1R",
        title="Consumer Duty — Act to deliver good outcomes for retail customers",
        text=(
            "A firm must act to deliver good outcomes for retail customers, including acting in "
            "good faith, avoiding causing foreseeable harm, and enabling customers to pursue their "
            "financial objectives. Dark patterns or hidden fees breach Consumer Duty."
        ),
        url="https://www.handbook.fca.org.uk/handbook/PRIN/2A/2.html",
    ),
]

_CCA_CORPUS: list[RegulatoryPassage] = [
    RegulatoryPassage(
        source="consumer_credit_act",
        reference="CCA 1974 s.55",
        title="Disclosure of pre-contract information",
        text=(
            "Regulations require prescribed pre-contract credit information (SECCI) to be disclosed "
            "in good time before a regulated agreement is made; failure renders the agreement "
            "enforceable only on an order of the court."
        ),
        url="https://www.legislation.gov.uk/ukpga/1974/39/section/55",
    ),
    RegulatoryPassage(
        source="consumer_credit_act",
        reference="CCA 1974 s.66A",
        title="Statutory 14-day right of withdrawal",
        text=(
            "The debtor under a regulated consumer credit agreement has a statutory right to "
            "withdraw from the agreement without giving any reason within 14 calendar days starting "
            "the day after the agreement is made or prescribed information is received. This period "
            "cannot be shortened contractually."
        ),
        url="https://www.legislation.gov.uk/ukpga/1974/39/section/66A",
    ),
    RegulatoryPassage(
        source="consumer_credit_act",
        reference="CCA 1974 s.75",
        title="Liability of creditor for breaches by supplier",
        text=(
            "Where a debtor has a claim for misrepresentation or breach of contract against a "
            "supplier for a financed item priced over £100 and not exceeding £30,000, the creditor "
            "is jointly and severally liable. Terms purporting to waive Section 75 liability are void."
        ),
        url="https://www.legislation.gov.uk/ukpga/1974/39/section/75",
    ),
    RegulatoryPassage(
        source="consumer_credit_act",
        reference="CCA 1974 s.87",
        title="Need for Default Notice before termination or enforcement",
        text=(
            "Service of a statutory Default Notice in prescribed form is necessary before the "
            "creditor can terminate the agreement, demand earlier payment, or enforce security. "
            "Under s.88, the notice must allow not less than 14 days to remedy the breach."
        ),
        url="https://www.legislation.gov.uk/ukpga/1974/39/section/87",
    ),
    RegulatoryPassage(
        source="consumer_credit_act",
        reference="CCA 1974 s.140A",
        title="Unfair relationships between creditors and debtors",
        text=(
            "A court may determine that the relationship between a creditor and debtor is unfair "
            "because of any terms of the agreement, the way the creditor exercises or enforces "
            "rights, or anything done or omitted on behalf of the creditor."
        ),
        url="https://www.legislation.gov.uk/ukpga/1974/39/section/140A",
    ),
]


# Public read-only view of the FCA mock corpus, used by the regulatory MCP server's search index.
FCA_CORPUS: tuple[RegulatoryPassage, ...] = tuple(_FCA_CORPUS)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 2}


def _score_passage(query: str, passage: RegulatoryPassage) -> float:
    q_tokens = _tokens(query)
    if not q_tokens:
        return 0.0
    p_tokens = _tokens(f"{passage.reference} {passage.title} {passage.text}")
    overlap = q_tokens & p_tokens
    return len(overlap) / max(len(q_tokens), 1)


def _as_mock(passage: RegulatoryPassage) -> RegulatoryPassage:
    return passage.model_copy(update={"metadata": {**passage.metadata, "corpus": "mock"}})


class MockFCAHandbookAdapter:
    """In-memory keyword-scored mock implementing `FCAHandbookPort`."""

    def __init__(self, passages: list[RegulatoryPassage] | None = None) -> None:
        self._passages = list(passages) if passages is not None else list(_FCA_CORPUS)

    async def search_rules(
        self,
        query: str,
        *,
        sourcebooks: list[str] | None = None,
        top_k: int = 5,
    ) -> list[RegulatoryPassage]:
        candidates = self._passages
        if sourcebooks:
            norm_books = [b.upper() for b in sourcebooks]
            filtered = [
                p for p in candidates if any(p.reference.upper().startswith(b) for b in norm_books)
            ]
            if filtered:
                candidates = filtered

        scored = [
            (score, passage.model_copy(update={"score": round(score, 3)}))
            for passage in candidates
            if (score := _score_passage(query, passage)) > 0
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return [p.model_copy(update={"score": 0.1}) for p in candidates[: min(2, top_k)]]
        return [p for _, p in scored[:top_k]]

    async def get_rule(self, reference: str) -> RegulatoryPassage | None:
        target = reference.strip().upper()
        for passage in self._passages:
            if passage.reference.strip().upper() == target:
                return passage
        return None


    # `FCAHandbookResearchPort`, for the fca_compliance_reviewer agent -----------------------------

    async def search(
        self,
        query: str,
        *,
        sourcebooks: list[str] | None = None,
        top_k: int = 8,
    ) -> RegulatorySearch:
        found = await self.search_rules(query, sourcebooks=sourcebooks, top_k=top_k)
        return RegulatorySearch(passages=[_as_mock(p) for p in found])

    async def get_section(self, reference: str) -> RegulatorySection:
        wanted = reference.strip().upper()
        section = ".".join(wanted.split(".")[:2])
        passages = [
            _as_mock(p)
            for p in self._passages
            if ".".join(p.reference.upper().split(".")[:2]) == section
        ]
        if not passages or (
            wanted != section and wanted not in {p.reference.upper() for p in passages}
        ):
            raise ValueError(f"{reference} is not in the mock Handbook. Do not cite it.")
        return RegulatorySection(section=section, passages=passages)

    async def check_citation(self, reference: str, quote: str) -> CitationCheck:
        rule = await self.get_rule(reference)
        if rule is None:
            return CitationCheck(ok=False, problems=[f"{reference} is not in the mock Handbook"])
        ok = len(quote.split()) >= 4 and " ".join(quote.split()) in rule.text
        return CitationCheck(
            ok=ok,
            problems=[] if ok else [f"the quote is not word for word in {reference}"],
            binding=rule.reference.endswith("R"),
            fictional=True,
        )


class MockConsumerCreditActAdapter:
    """In-memory mock implementing `ConsumerCreditActPort` (supports query + section fetch)."""

    def __init__(self, passages: list[RegulatoryPassage] | None = None) -> None:
        self._passages = list(passages) if passages is not None else list(_CCA_CORPUS)

    async def query_provisions(
        self,
        query: str,
        *,
        sections: list[str] | None = None,
        top_k: int = 5,
    ) -> list[RegulatoryPassage]:
        candidates = self._passages
        if sections:
            norm_secs = [s.lower().replace("section", "s.").replace(" ", "") for s in sections]
            matched = [
                p
                for p in candidates
                if any(sec in p.reference.lower().replace(" ", "") for sec in norm_secs)
            ]
            if matched:
                candidates = matched

        scored = [
            (score, passage.model_copy(update={"score": round(score, 3)}))
            for passage in candidates
            if (score := _score_passage(query, passage)) > 0
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return [p.model_copy(update={"score": 0.1}) for p in candidates[: min(2, top_k)]]
        return [p for _, p in scored[:top_k]]

    async def get_document(self, section: str | None = None) -> list[RegulatoryPassage]:
        if not section:
            return list(self._passages)
        norm = section.lower().replace("section", "s.").replace(" ", "")
        return [
            p for p in self._passages if norm in p.reference.lower().replace(" ", "")
        ]
