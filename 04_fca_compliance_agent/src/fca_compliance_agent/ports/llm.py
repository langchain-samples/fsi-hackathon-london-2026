"""Driven Port protocol for LLM-powered compliance reasoning.

Isolating LLM reasoning behind a port keeps Core graph nodes clean, testable without
network calls, and independent of any single model provider or structured-output API.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import (
    AnalysisInput,
    AnalysisUnit,
    Finding,
    RegulatoryPassage,
    TriagedUnit,
)


@runtime_checkable
class ComplianceAnalyzerPort(Protocol):
    """Outbound port encapsulating the three LLM reasoning steps in the Core subgraph."""

    async def triage_units(
        self,
        analysis_input: AnalysisInput,
    ) -> list[TriagedUnit]:
        """Decide which AnalysisUnits warrant regulatory inspection and generate search queries."""
        ...

    async def analyze_unit(
        self,
        *,
        unit: AnalysisUnit,
        analysis_input_summary: str,
        fca_passages: list[RegulatoryPassage],
        cca_passages: list[RegulatoryPassage],
    ) -> list[Finding]:
        """Compare a single unit against retrieved FCA Handbook rules & CCA provisions."""
        ...

    async def synthesize_executive_summary(
        self,
        *,
        analysis_input: AnalysisInput,
        findings: list[Finding],
    ) -> str:
        """Produce a concise executive summary across deduplicated findings."""
        ...
