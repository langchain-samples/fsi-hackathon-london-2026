"""Composition helpers wiring Inbound -> Core Subgraph -> HITL Gate -> Outbound."""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..domain.state import PipelineConfig, PipelineInput, PipelineState
from ..ports.adapters import ContextModule, InboundAdapterNode, OutboundAdapterNode
from .nodes import make_human_review_gate_node, route_after_human_review


def build_compliance_pipeline(
    *,
    inbound_adapter: InboundAdapterNode,
    core_subgraph: CompiledStateGraph,
    outbound_adapter: OutboundAdapterNode,
    default_require_human_approval: bool = False,
    checkpointer: Any = None,
    **compile_kwargs: Any,
) -> CompiledStateGraph:
    """Compose a full compliance pipeline from swappable LangGraph nodes/subgraphs.

    Topology:
        START
          -> inbound_adapter     (node or subgraph: raw_input -> analysis_input + adapter_context)
          -> core_analysis       (compiled core subgraph: analysis_input -> findings_report)
          -> human_review_gate   (optional LangGraph interrupt() checkpoint)
          -> outbound_adapter    (node or agentic subgraph: findings_report + adapter_context -> deliverable_result)
          -> END
    """
    builder = StateGraph(
        state_schema=PipelineState,
        context_schema=PipelineConfig,
        input_schema=PipelineInput,
    )

    builder.add_node("inbound_adapter", inbound_adapter)
    builder.add_node("core_analysis", core_subgraph)
    builder.add_node(
        "human_review_gate",
        make_human_review_gate_node(
            default_require_human_approval=default_require_human_approval
        ),
    )
    builder.add_node("outbound_adapter", outbound_adapter)

    builder.add_edge(START, "inbound_adapter")
    builder.add_edge("inbound_adapter", "core_analysis")
    builder.add_edge("core_analysis", "human_review_gate")
    builder.add_conditional_edges(
        "human_review_gate",
        route_after_human_review,
        {"outbound_adapter": "outbound_adapter", END: END},
    )
    builder.add_edge("outbound_adapter", END)

    return builder.compile(checkpointer=checkpointer, **compile_kwargs)


def build_pipeline_for_module(
    module: ContextModule,
    *,
    core_subgraph: CompiledStateGraph,
    default_require_human_approval: bool = False,
    checkpointer: Any = None,
    **compile_kwargs: Any,
) -> CompiledStateGraph:
    """Convenience builder accepting a bundled `ContextModule` I/O pairing."""
    return build_compliance_pipeline(
        inbound_adapter=module.inbound_node,
        core_subgraph=core_subgraph,
        outbound_adapter=module.outbound_node,
        default_require_human_approval=default_require_human_approval,
        checkpointer=checkpointer,
        **compile_kwargs,
    )
