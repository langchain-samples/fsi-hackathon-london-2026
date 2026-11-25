"""Pluggable Inbound and Outbound Adapter node contracts.

Both `InboundAdapterNode` and `OutboundAdapterNode` accept EITHER:
  - A plain sync/async Python callable `(state: PipelineState) -> dict[str, Any]`
  - A compiled LangGraph `StateGraph` (including full agentic subgraphs with private tools!)

This allows simple deterministic adapters (e.g. PDF report renderer) and complex
agentic adapters (e.g. Codebase Remediation sub-agent with file edit + git + gh tools)
to plug into the exact same pipeline composition slot.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeAlias

from ..domain.state import PipelineState

# Compatible with LangGraph `StateGraph.add_node(...)`: sync/async functions OR CompiledStateGraph / Runnable.
InboundAdapterNode: TypeAlias = (
    Callable[[PipelineState], dict[str, Any] | Awaitable[dict[str, Any]]] | Any
)
OutboundAdapterNode: TypeAlias = (
    Callable[[PipelineState], dict[str, Any] | Awaitable[dict[str, Any]]] | Any
)


@dataclass(frozen=True, slots=True)
class ContextModule:
    """An interchangeable I/O adapter pairing for a specific compliance context.

    Examples:
      - `pr_review`: PR link via `gh` -> inline PR comments via `gh`
      - `codebase_review`: GitHub repo URL -> agentic remediation PR via git + `gh`
      - `product_spec_review`: Incoming PDF -> PDF findings report
    """

    name: str
    description: str
    inbound_node: InboundAdapterNode
    outbound_node: OutboundAdapterNode
