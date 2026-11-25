"""MCP-backed Driven Adapters for the external FCA Handbook RAG & Consumer Credit Act servers.

Supports BOTH MCP transports:
  - `stdio`           (local subprocess server via command + args)
  - `streamable_http` (remote HTTP MCP endpoint via url + headers)

Because the external MCP servers are delivered by another team, tool names, argument keys,
and response formats are fully configurable via `MCPToolMapping` without modifying the Core.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

try:
    # `mcp` imports this in the middle of its first call, which reads files. `langgraph dev` refuses that in async code.
    import jsonschema  # noqa: F401
except ImportError:  # the `mcp` extra is not installed
    pass

from ...domain.models import (
    CitationCheck,
    RegulatoryPassage,
    RegulatorySearch,
    RegulatorySection,
    RegulatorySource,
)

# Low-level async MCP tool invocation callable (allows injecting a fake transport in unit tests).
MCPCaller = Callable[[str, dict[str, Any]], Awaitable[Any]]


SNIPPET_END = "[...]"  # how the Handbook RAG marks text that it cut short
NO_SUCH_SECTION = "has no section"  # how `fca-regulatory-mcp` reports a CCA section that does not exist


class MCPToolError(RuntimeError):
    """An MCP tool call failed. Its message must never be used as regulatory evidence."""


def _raise_if_error(raw_result: Any) -> None:
    """Raise if the server reported a failure (`is_error` in mcp 2.x, `isError` in mcp 1.x and JSON)."""
    if isinstance(raw_result, dict):
        failed = raw_result.get("is_error") is True or raw_result.get("isError") is True
        content = raw_result.get("content") if failed else None
    else:
        failed = (
            getattr(raw_result, "is_error", None) is True
            or getattr(raw_result, "isError", None) is True
        )
        content = getattr(raw_result, "content", None) if failed else None
    if not failed:
        return
    blocks = content if isinstance(content, list) else [content]
    texts = [
        block.get("text") if isinstance(block, dict) else getattr(block, "text", block)
        for block in blocks
    ]
    message = "\n".join(str(text) for text in texts if text)
    raise MCPToolError(message or "The MCP tool reported an error without a message.")


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
    section_tool: str | None = None  # only name tools that the server has
    citation_tool: str | None = None


def _unwrap_result(structured: dict[str, Any]) -> Any:
    """The SDK puts a tool's list or text under `result`, because structured content has to be an object."""
    return structured["result"] if set(structured) == {"result"} else structured


def _unwrap(raw_result: Any) -> Any:
    """What a tool returned, taken out of its MCP envelope: an SDK object, or the same thing as raw JSON."""
    if isinstance(raw_result, dict):
        blocks = raw_result.get("content")
        enveloped = isinstance(blocks, list) and all(isinstance(b, dict) and "type" in b for b in blocks)
        structured = raw_result.get("structuredContent") or raw_result.get("structured_content")
        if isinstance(structured, dict):
            return _unwrap_result(structured)
        if not enveloped:
            return raw_result
    else:
        for name in ("structured_content", "structuredContent"):
            if isinstance(structured := getattr(raw_result, name, None), dict):
                return _unwrap_result(structured)
        blocks = getattr(raw_result, "content", None)
        if not isinstance(blocks, list):
            return raw_result
    texts = [
        block if isinstance(block, str) else block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        for block in blocks
    ]
    text = "\n".join(t for t in texts if t).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _structured(raw_result: Any, *required: str) -> dict[str, Any]:
    """The JSON object a tool returned. Raises `MCPToolError` for an error, another shape or a missing key."""
    _raise_if_error(raw_result)
    payload = _unwrap(raw_result)
    if not isinstance(payload, dict):
        raise MCPToolError("The MCP tool did not return a JSON object.")
    if missing := [key for key in required if key not in payload]:
        raise MCPToolError(f"The MCP tool's result has no {', '.join(missing)}.")
    return payload


def _is_unknown_tool(error: MCPToolError) -> bool:
    return str(error).startswith("Unknown tool")


def _compact(reference: str) -> str:
    return "".join(reference.upper().split())


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

    An error gives no passages, because its message is not evidence. Callers that must tell a failure
    from an empty result call `_raise_if_error` first.
    """
    payload = raw_result

    # Ignore MCP tool-level errors (e.g. an unknown CCA section requested by triage)
    try:
        _raise_if_error(payload)
    except MCPToolError:
        return []

    # Unwrap official MCP SDK CallToolResult-like objects if present
    payload = _unwrap(payload)

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
            fetch_tool=None,
            query_arg="query",
            top_k_arg="top_k",
            filter_arg="sourcebooks",
            reference_arg="reference",
            section_tool="get_fca_section",
            citation_tool="check_fca_citation",
        )

    async def search_rules(
        self,
        query: str,
        *,
        sourcebooks: list[str] | None = None,
        top_k: int = 5,
    ) -> list[RegulatoryPassage]:
        found = await self.search_rules_in_scope(query, sourcebooks=sourcebooks, top_k=top_k)
        return found.passages

    async def search_rules_in_scope(
        self,
        query: str,
        *,
        sourcebooks: list[str] | None = None,
        top_k: int = 5,
    ) -> RegulatorySearch:
        """`search_rules`, and also which of `sourcebooks` the server could not search."""
        args: dict[str, Any] = {self._mapping.query_arg: query}
        if self._mapping.top_k_arg:
            args[self._mapping.top_k_arg] = top_k
        if self._mapping.filter_arg and sourcebooks:
            args[self._mapping.filter_arg] = sourcebooks

        raw = await self._call_tool(self._mapping.search_tool, args)
        _raise_if_error(raw)
        payload = _unwrap(raw)
        passages = parse_mcp_passages(payload, source="fca_handbook", default_reference="FCA")
        return RegulatorySearch(
            passages=list(await asyncio.gather(*(self._in_full(p) for p in passages))),
            not_in_index=payload.get("not_in_index", []) if isinstance(payload, dict) else [],
        )

    async def _in_full(self, passage: RegulatoryPassage) -> RegulatoryPassage:
        """Callers of `search_rules` judge on its text, so a snippet is replaced by the whole provision."""
        if not passage.text.endswith(SNIPPET_END) or not self._mapping.section_tool:
            return passage
        section = await self.get_section(passage.reference)
        return next((p for p in section.passages if p.reference == passage.reference), passage)

    async def get_rule(self, reference: str) -> RegulatoryPassage | None:
        if self._mapping.fetch_tool:
            raw = await self._call_tool(
                self._mapping.fetch_tool,
                {self._mapping.reference_arg: reference},
            )
            try:
                _raise_if_error(raw)
            except MCPToolError as error:
                if not _is_unknown_tool(error) or not self._mapping.section_tool:
                    raise
                return await self._rule_from_section(reference)  # the Handbook RAG reads whole sections instead
            passages = parse_mcp_passages(raw, source="fca_handbook", default_reference=reference)
            return passages[0] if passages else None

        if self._mapping.section_tool:
            return await self._rule_from_section(reference)

        # Graceful fallback if the MCP server only exposes semantic search
        results = await self.search_rules(reference, top_k=1)
        return results[0] if results else None

    async def _rule_from_section(self, reference: str) -> RegulatoryPassage | None:
        try:
            section = await self.get_section(reference)
        except MCPToolError as error:
            if "not in force in the index" in str(error) or "is not a Handbook reference" in str(error):
                return None
            raise
        # Never a neighbour: the exact reference, or the same one without its type ("CONC 5.2A.4" for "CONC 5.2A.4R").
        wanted = _compact(reference)
        exact = [p for p in section.passages if _compact(p.reference) == wanted]
        untyped = [
            p
            for p in section.passages
            if wanted + str(p.metadata.get("provision_type") or "") == _compact(p.reference)
        ]
        return next(iter(exact + untyped), None)

    # `FCAHandbookResearchPort`, for a reviewing agent ------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        sourcebooks: list[str] | None = None,
        top_k: int = 8,
    ) -> RegulatorySearch:
        args: dict[str, Any] = {self._mapping.query_arg: query}
        if self._mapping.top_k_arg:
            args[self._mapping.top_k_arg] = top_k
        if self._mapping.filter_arg and sourcebooks:
            args[self._mapping.filter_arg] = sourcebooks
        payload = _structured(await self._call_tool(self._mapping.search_tool, args), "results")
        return RegulatorySearch(
            passages=parse_mcp_passages(
                {"results": payload["results"]}, source="fca_handbook", default_reference="FCA"
            ),
            not_in_index=payload.get("not_in_index", []),
        )

    async def get_section(self, reference: str) -> RegulatorySection:
        if not self._mapping.section_tool:
            raise MCPToolError("This MCP server has no tool for reading a section.")
        payload = _structured(
            await self._call_tool(
                self._mapping.section_tool, {self._mapping.reference_arg: reference}
            ),
            "section", "results", "complete",
        )
        return RegulatorySection(
            section=payload["section"],
            passages=parse_mcp_passages(
                {"results": payload["results"]}, source="fca_handbook", default_reference=reference
            ),
            complete=payload["complete"],
            outline=payload.get("outline", []),
            definitions=payload.get("definitions", {}),
            links=payload.get("links", []),
        )

    async def check_citation(self, reference: str, quote: str) -> CitationCheck:
        if not self._mapping.citation_tool:
            raise MCPToolError("This MCP server has no tool for checking a citation.")
        payload = _structured(
            await self._call_tool(
                self._mapping.citation_tool,
                {self._mapping.reference_arg: reference, "quote": quote},
            ),
            "ok",
        )
        return CitationCheck(
            ok=payload["ok"],
            problems=payload.get("problems", []),
            binding=payload.get("binding"),
            fictional=payload.get("source") == "fictional-sample",
        )


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
            _raise_if_error(raw)
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
            try:
                _raise_if_error(raw)
            except MCPToolError as error:
                # Triage can guess a section that does not exist. Any other failure must not pass unnoticed.
                if section is not None and NO_SUCH_SECTION in str(error):
                    return []
                raise
            return parse_mcp_passages(
                raw,
                source="consumer_credit_act",
                default_reference=section or "CCA 1974",
            )

        # Fallback if the server only exposes query search
        return await self.query_provisions(section or "Consumer Credit Act 1974")
