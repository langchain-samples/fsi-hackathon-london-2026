"""Gives a LangChain agent the Handbook tools from a running `mcp_server.py`.

    agent = create_deep_agent(model=..., tools=load_handbook_tools(), ...)

It is self-contained, so the core agent's project can copy this one file.
"""

import asyncio
import json
import os
import threading
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from mcp import Client

DEFAULT_URL = "http://127.0.0.1:8765/mcp"


def _run(coroutine) -> Any:
    """Run a coroutine from sync code, even if this thread already has an event loop, as notebooks do."""
    outcome: dict = {}

    def work() -> None:
        try:
            outcome["value"] = asyncio.run(coroutine)
        except BaseException as error:  # noqa: BLE001 (re-raised below, in the caller's thread)
            outcome["error"] = error

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def call_handbook(name: str, arguments: dict, server: Any = None) -> Any:
    """Call one tool from plain code, for example `call_handbook("check_citation", {...})` after the agent answers."""
    return _run(_call(server or os.environ.get("HANDBOOK_MCP_URL", DEFAULT_URL), name, arguments))


async def _call(server: Any, name: str, arguments: dict) -> Any:
    async with Client(server) as client:
        result = await client.call_tool(name, arguments)
    text = "\n".join(block.text for block in result.content if block.type == "text")
    if result.is_error:
        raise ToolException(text or f"{name} failed")
    if result.structured_content is not None:
        content = result.structured_content
        return content["result"] if set(content) == {"result"} else content
    return text


def _as_langchain(server: Any, tool: Any) -> BaseTool:
    async def acall(**arguments: Any) -> str:
        given = {k: v for k, v in arguments.items() if v is not None}
        result = await _call(server, tool.name, given)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

    def call(**arguments: Any) -> str:
        return _run(acall(**arguments))

    return StructuredTool(
        name=tool.name, description=tool.description or tool.name, args_schema=tool.input_schema,
        func=call, coroutine=acall, handle_tool_error=True,
    )  # fmt: skip


def load_handbook_tools(server: Any = None) -> list[BaseTool]:
    """Every tool the server offers. The server must be running: start it with `uv run --no-sync python mcp_server.py`."""
    target = server or os.environ.get("HANDBOOK_MCP_URL", DEFAULT_URL)

    async def fetch() -> list:
        async with Client(target) as client:
            return (await client.list_tools()).tools

    return [_as_langchain(target, tool) for tool in _run(fetch())]
