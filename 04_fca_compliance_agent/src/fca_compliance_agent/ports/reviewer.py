"""Driven Port protocol for the agent that reviews one unit against the FCA Handbook and the CCA."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..domain.models import AnalysisUnit, TriagedUnit, UnitReview


@runtime_checkable
class ComplianceReviewerPort(Protocol):
    """Reviews one unit, choosing for itself what to search, read and search again."""

    async def review_unit(
        self,
        *,
        unit: AnalysisUnit,
        analysis_input_summary: str,
        triage: TriagedUnit,
        regulatory_scope: list[str],
    ) -> UnitReview:
        """Return a decision and draft findings. Code checks them afterwards."""
        ...
