"""Driven Port protocols for external regulatory knowledge sources.

Implemented externally via MCP servers (stdio or Streamable HTTP) in production,
or via in-memory mock adapters for offline development and testing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import CitationCheck, RegulatoryPassage, RegulatorySearch, RegulatorySection


@runtime_checkable
class FCAHandbookPort(Protocol):
    """Outbound port for querying the FCA Handbook RAG service."""

    async def search_rules(
        self,
        query: str,
        *,
        sourcebooks: list[str] | None = None,
        top_k: int = 5,
    ) -> list[RegulatoryPassage]:
        """Semantic or keyword search over FCA Handbook rules and guidance."""
        ...

    async def get_rule(self, reference: str) -> RegulatoryPassage | None:
        """Fetch a specific FCA Handbook rule by canonical reference (e.g. 'CONC 4.2.5R')."""
        ...


@runtime_checkable
class FCAScopedSearchPort(Protocol):
    """Optional: an `FCAHandbookPort` that also says which requested sourcebooks it could not search."""

    async def search_rules_in_scope(
        self,
        query: str,
        *,
        sourcebooks: list[str] | None = None,
        top_k: int = 5,
    ) -> RegulatorySearch:
        """Whole rules, as `search_rules` gives, and any requested sourcebook that is not in the index."""
        ...


@runtime_checkable
class FCAHandbookResearchPort(Protocol):
    """What the Handbook RAG offers a reviewing agent: search widely, read in depth, check the quote."""

    async def search(
        self,
        query: str,
        *,
        sourcebooks: list[str] | None = None,
        top_k: int = 8,
    ) -> RegulatorySearch:
        """Candidate provisions. The text may be a snippet."""
        ...

    async def get_section(self, reference: str) -> RegulatorySection:
        """The whole section a provision belongs to. Raises if it is not in the Handbook index."""
        ...

    async def check_citation(self, reference: str, quote: str) -> CitationCheck:
        """Check without a model that a provision is in force and the quote is word for word."""
        ...


@runtime_checkable
class ConsumerCreditActPort(Protocol):
    """Outbound port for querying or retrieving Consumer Credit Act 1974 provisions.

    Intentionally supports both query-based retrieval AND direct section/document fetch
    so any MCP server implementation (RAG search, section lookup, or full-document tool)
    can satisfy this port without changing Core code.
    """

    async def query_provisions(
        self,
        query: str,
        *,
        sections: list[str] | None = None,
        top_k: int = 5,
    ) -> list[RegulatoryPassage]:
        """Search Consumer Credit Act provisions relevant to a query."""
        ...

    async def get_document(
        self,
        section: str | None = None,
    ) -> list[RegulatoryPassage]:
        """Retrieve a specific CCA section or full/summary Act passages."""
        ...
