"""Builder for the encapsulated Core Compliance Analysis LangGraph Subgraph."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..domain.state import CoreAnalysisState
from ..ports.llm import ComplianceAnalyzerPort
from ..ports.regulatory import ConsumerCreditActPort, FCAHandbookPort
from ..ports.reviewer import ComplianceReviewerPort
from .nodes import (
    make_analyze_unit_node,
    make_synthesize_report_node,
    make_triage_node,
    route_triaged_units,
)
from .review import make_review_unit_node


def build_core_analysis_subgraph(
    *,
    fca_port: FCAHandbookPort,
    cca_port: ConsumerCreditActPort,
    analyzer_port: ComplianceAnalyzerPort,
    reviewer_port: ComplianceReviewerPort | None = None,
    max_passages_per_query: int = 5,
    **compile_kwargs: Any,
) -> CompiledStateGraph:
    """Build and compile the context-agnostic Core Compliance Analysis subgraph.

    Topology:
        START -> triage_units --(parallel Send per relevant unit)--> analyze_unit -> synthesize_report -> END
                              --(if 0 relevant units)------------------------------> synthesize_report -> END

    With a `reviewer_port`, `analyze_unit` runs that agent and then checks its work in code; `fca_port` must
    then also implement `FCAHandbookResearchPort`. Without one, it is a fixed retrieve-then-ask step.
    Triage and the executive summary are single LLM calls either way.

    State contract when composed into `PipelineState`:
        - Reads:  `analysis_input`
        - Writes: `findings_report`
    """
    builder = StateGraph(CoreAnalysisState)

    builder.add_node("triage_units", make_triage_node(analyzer_port))
    if reviewer_port is not None:
        analyze_unit = make_review_unit_node(reviewer_port=reviewer_port, fca_port=fca_port)
    else:
        analyze_unit = make_analyze_unit_node(
            fca_port=fca_port,
            cca_port=cca_port,
            analyzer_port=analyzer_port,
            max_passages_per_query=max_passages_per_query,
        )
    builder.add_node("analyze_unit", analyze_unit)
    builder.add_node("synthesize_report", make_synthesize_report_node(analyzer_port))

    builder.add_edge(START, "triage_units")
    builder.add_conditional_edges(
        "triage_units",
        route_triaged_units,
        ["analyze_unit", "synthesize_report"],
    )
    builder.add_edge("analyze_unit", "synthesize_report")
    builder.add_edge("synthesize_report", END)

    return builder.compile(**compile_kwargs)
