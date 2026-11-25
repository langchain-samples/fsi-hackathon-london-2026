"""LangGraph state schemas enforcing hexagonal boundaries between Pipeline and Core.

Key isolation guarantee:
- `PipelineState` carries `adapter_context` (e.g. local repo path, PR number, output PDF path)
  from the Inbound Adapter directly to the Outbound Adapter.
- `CoreAnalysisState` intentionally omits `adapter_context` and `raw_input`, sharing ONLY
  `analysis_input` (in) and `findings_report` (out) with `PipelineState`.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict

from .models import (
    AnalysisInput,
    AnalysisUnit,
    DeliverableResult,
    Finding,
    FindingsReport,
    HumanReviewDecision,
    OutputType,
    TriagedUnit,
)


class PipelineConfig(TypedDict, total=False):
    """Runtime configuration passed via LangGraph `context` / configurable parameters."""

    require_human_approval: bool
    max_passages_per_query: int
    regulatory_scope: list[str]
    model_name: str


class CoreAnalysisState(TypedDict, total=False):
    """Encapsulated state of the Core Compliance Analysis Subgraph.

    Shared keys with `PipelineState`:
      - Input:  `analysis_input`
      - Output: `findings_report`

    Private keys (never leak into `PipelineState`):
      - `triaged_units`, `unit_findings`, `queries_executed`, `scope_not_searched`
    """

    analysis_input: AnalysisInput
    triaged_units: list[TriagedUnit]
    unit_findings: Annotated[list[Finding], operator.add]
    queries_executed: Annotated[list[str], operator.add]
    scope_not_searched: Annotated[list[str], operator.add]
    findings_report: FindingsReport


class UnitAnalysisState(TypedDict):
    """Isolated per-unit worker state dispatched via LangGraph `Send` API."""

    analysis_input_summary: str
    regulatory_scope: list[str]
    unit: AnalysisUnit
    triage: TriagedUnit


class PipelineInput(TypedDict, total=False):
    """Public input schema for invoking any composed compliance pipeline."""

    raw_input: dict[str, Any]
    require_human_approval: bool


class PipelineState(TypedDict, total=False):
    """Top-level state flowing across Inbound -> Core -> HITL Gate -> Outbound."""

    raw_input: dict[str, Any]
    require_human_approval: bool
    analysis_input: AnalysisInput
    adapter_context: dict[str, Any]
    output_type: OutputType  # decided by the inbound adapter; the core never reads it
    findings_report: FindingsReport
    human_review: HumanReviewDecision
    deliverable_result: DeliverableResult
