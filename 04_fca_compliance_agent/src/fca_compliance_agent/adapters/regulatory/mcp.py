"""MCP-backed Driven Adapters for the external FCA Handbook RAG & Consumer Credit Act servers.

Supports BOTH MCP transports:
  - `stdio`           (local subprocess server via command + args)
  - `streamable_http` (remote HTTP MCP endpoint via url + headers)

Because the external MCP servers are delivered by another team, tool names, argument keys,
and response formats are fully configurable via `MCPToolMapping` without modifying the Core.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from ...domain.models import RegulatoryPassage, RegulatorySource

# Low-level async MCP tool invocation callable (allows injecting a fake transport in unit tests).
MCPCaller = Callable[[str, dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    """Connection settings for an external MCP server (stdio or Streamable HTTP)."""

    transport: Literal["stdio", "streamable_http"] = "streamable_http"
    # Streamable HTTP fields
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    # Stdio fields
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class MCPToolMapping:
    """Maps Port methods to the external MCP server's tool names and argument keys."""

    search_tool: str
    fetch_tool: str | None = None
    query_arg: str = "query"
    top_k_arg: str | None = "top_k"
    filter_arg: str | None = None
    reference_arg: str = "reference"


def parse_mcp_passages(
    raw_result: Any,
    *,
    source: RegulatorySource,
    default_reference: str = "UNKNOWN",
) -> list[RegulatoryPassage]:
    """Normalise heterogeneous MCP tool payloads into `list[RegulatoryPassage]`.

    Handles:
      - MCP `CallToolResult` objects with `.content` blocks (text / JSON)
      - Python lists of dicts or strings
      - Single dicts or plain-text markdown strings
    """
    payload = raw_result

    # Ignore MCP tool-level errors (e.g. an unknown CCA section requested by triage)
    if getattr(payload, "isError", False):
        return []

    # Unwrap official MCP SDK CallToolResult-like objects if present
    if hasattr(payload, "structuredContent") and payload.structuredContent is not None:
        payload = payload.structuredContent
    elif hasattr(payload, "content") and isinstance(payload.content, list):
        texts = [
            getattr(block, "text", str(block))
            for block in payload.content
            if getattr(block, "text", None) is not None or isinstance(block, str)
        ]
        combined = "\n".join(texts).strip()
        if not combined:
            return []
        try:
            payload = json.loads(combined)
        except json.JSONDecodeError:
            payload = combined

    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return [
                RegulatoryPassage(
                    source=source,
                    reference=default_reference,
                    title=default_reference,
                    text=text,
                )
            ]

    if isinstance(payload, dict):
        # Support wrapper objects like {"results": [...]} or {"passages": [...]} or {"documents": [...]}
        for container_key in ("results", "passages", "documents", "items", "chunks"):
            if container_key in payload and isinstance(payload[container_key], list):
                payload = payload[container_key]
                break
        else:
            payload = [payload]

    if not isinstance(payload, list):
        return [
            RegulatoryPassage(
                source=source,
                reference=default_reference,
                title=default_reference,
                text=str(payload),
            )
        ]

    passages: list[RegulatoryPassage] = []
    for idx, item in enumerate(payload):
        if isinstance(item, RegulatoryPassage):
            passages.append(item)
        elif isinstance(item, str):
            passages.append(
                RegulatoryPassage(
                    source=source,
                    reference=f"{default_reference}#{idx + 1}",
                    title=default_reference,
                    text=item,
                )
            )
        elif isinstance(item, dict):
            ref = str(
                item.get("reference")
                or item.get("rule_ref")
                or item.get("section")
                or item.get("id")
                or f"{default_reference}#{idx + 1}"
            )
            text = str(
                item.get("text")
                or item.get("content")
                or item.get("page_content")
                or item.get("excerpt")
                or ""
            )
            if not text:
                continue
            passages.append(
                RegulatoryPassage(
                    source=source,
                    reference=ref,
                    title=str(item.get("title") or item.get("heading") or ref),
                    text=text,
                    url=item.get("url"),
                    score=float(item["score"]) if item.get("score") is not None else None,
                    metadata={
                        k: v
                        for k, v in item.items()
                        if k
                        not in {
                            "reference",
                            "rule_ref",
                            "section",
                            "id",
                            "text",
                            "content",
                            "page_content",
                            "excerpt",
                            "title",
                            "heading",
                            "url",
                            "score",
                        }
                    },
                )
            )
    return passages


class _BaseMCPClientAdapter:
    """Shared MCP invocation helper supporting stdio, Streamable HTTP, or injected caller."""

    def __init__(
        self,
        *,
        server_config: MCPServerConfig | None = None,
        caller: MCPCaller | None = None,
    ) -> None:
        if server_config is None and caller is None:
            raise ValueError("Provide either `server_config` or a custom async `caller`.")
        self._server_config = server_config
        self._caller = caller

    async def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if self._caller is not None:
            return await self._caller(tool_name, arguments)

        assert self._server_config is not None
        cfg = self._server_config

        try:
            from mcp import ClientSession
        except ImportError as exc:
            raise RuntimeError(
                "The `mcp` package is required for live MCP server calls. "
                "Install it with `uv sync --all-packages --extra mcp`."
            ) from exc

        if cfg.transport == "stdio":
            if not cfg.command:
                raise ValueError("MCPServerConfig.command is required when transport='stdio'.")
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=cfg.command,
                args=list(cfg.args),
                env=cfg.env,
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    return await session.call_tool(tool_name, arguments)

        if cfg.transport == "streamable_http":
            if not cfg.url:
                raise ValueError(
                    "MCPServerConfig.url is required when transport='streamable_http'."
                )
            from mcp.client import streamable_http as sh_mod

            if hasattr(sh_mod, "streamable_http_client"):
                # mcp >= 2.0 API
                http_client = (
                    sh_mod.create_mcp_http_client(headers=cfg.headers)
                    if cfg.headers
                    else None
                )
                async with sh_mod.streamable_http_client(
                    cfg.url,
                    http_client=http_client,
                ) as streams:
                    read_stream, write_stream = streams[0], streams[1]
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        return await session.call_tool(tool_name, arguments)
            else:
                # mcp 1.x fallback API
                async with sh_mod.streamablehttp_client(
                    cfg.url,
                    headers=cfg.headers or None,
                ) as streams:
                    read_stream, write_stream = streams[0], streams[1]
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        return await session.call_tool(tool_name, arguments)

        raise ValueError(f"Unsupported MCP transport: {cfg.transport!r}")


class MCPFCAHandbookAdapter(_BaseMCPClientAdapter):
    """MCP adapter implementing `FCAHandbookPort` against the external FCA RAG MCP server."""

    def __init__(
        self,
        *,
        server_config: MCPServerConfig | None = None,
        tool_mapping: MCPToolMapping | None = None,
        caller: MCPCaller | None = None,
    ) -> None:
        super().__init__(server_config=server_config, caller=caller)
        self._mapping = tool_mapping or MCPToolMapping(
            search_tool="search_fca_handbook",
            fetch_tool="get_fca_rule",
            query_arg="query",
            top_k_arg="top_k",
            filter_arg="sourcebooks",
            reference_arg="reference",
        )

    async def search_rules(
        self,
        query: str,
        *,
        sourcebooks: list[str] | None = None,
        top_k: int = 5,
    ) -> list[RegulatoryPassage]:
        args: dict[str, Any] = {self._mapping.query_arg: query}
        if self._mapping.top_k_arg:
            args[self._mapping.top_k_arg] = top_k
        if self._mapping.filter_arg and sourcebooks:
            args[self._mapping.filter_arg] = sourcebooks

        raw = await self._call_tool(self._mapping.search_tool, args)
        return parse_mcp_passages(raw, source="fca_handbook", default_reference="FCA")

    async def get_rule(self, reference: str) -> RegulatoryPassage | None:
        if self._mapping.fetch_tool:
            raw = await self._call_tool(
                self._mapping.fetch_tool,
                {self._mapping.reference_arg: reference},
            )
            passages = parse_mcp_passages(raw, source="fca_handbook", default_reference=reference)
            return passages[0] if passages else None

        # Graceful fallback if the MCP server only exposes semantic search
        results = await self.search_rules(reference, top_k=1)
        return results[0] if results else None


class MCPConsumerCreditActAdapter(_BaseMCPClientAdapter):
    """MCP adapter implementing `ConsumerCreditActPort`.

    Flexibly adapts to whichever tool shape the external CCA MCP server exposes:
      - RAG / search tool (`search_tool`)
      - Section / full-document fetch tool (`fetch_tool`)
      - Or both!
    """

    def __init__(
        self,
        *,
        server_config: MCPServerConfig | None = None,
        tool_mapping: MCPToolMapping | None = None,
        caller: MCPCaller | None = None,
    ) -> None:
        super().__init__(server_config=server_config, caller=caller)
        self._mapping = tool_mapping or MCPToolMapping(
            search_tool="query_consumer_credit_act",
            fetch_tool="get_consumer_credit_act_document",
            query_arg="query",
            top_k_arg="top_k",
            filter_arg="sections",
            reference_arg="section",
        )

    async def query_provisions(
        self,
        query: str,
        *,
        sections: list[str] | None = None,
        top_k: int = 5,
    ) -> list[RegulatoryPassage]:
        if self._mapping.search_tool:
            args: dict[str, Any] = {self._mapping.query_arg: query}
            if self._mapping.top_k_arg:
                args[self._mapping.top_k_arg] = top_k
            if self._mapping.filter_arg and sections:
                args[self._mapping.filter_arg] = sections
            raw = await self._call_tool(self._mapping.search_tool, args)
            return parse_mcp_passages(
                raw, source="consumer_credit_act", default_reference="CCA 1974"
            )

        # Fallback if the server only offers document fetch
        return await self.get_document(section=sections[0] if sections else None)

    async def get_document(self, section: str | None = None) -> list[RegulatoryPassage]:
        if self._mapping.fetch_tool:
            args: dict[str, Any] = {}
            if section is not None:
                args[self._mapping.reference_arg] = section
            raw = await self._call_tool(self._mapping.fetch_tool, args)
            return parse_mcp_passages(
                raw,
                source="consumer_credit_act",
                default_reference=section or "CCA 1974",
            )

        # Fallback if the server only exposes query search
        return await self.query_provisions(section or "Consumer Credit Act 1974")
