"""The Handbook RAG as an MCP server, so any agent can use it as a tool. Run it from this folder:

    uv run --no-sync python mcp_server.py            # HTTP on http://127.0.0.1:8765/mcp: one server that every agent call shares
    uv run --no-sync python mcp_server.py --stdio    # started by one MCP client, over stdin and stdout

Use HTTP for agents with parallel sub-agents: local Qdrant allows one process, and stdio clients may start several.
HTTP only listens on this machine. The server is read-only, and never loads FCA credentials.
"""

import argparse
import functools
import inspect
from datetime import date
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from tools.handbook import HANDBOOK_TOOLS, NO_MATCHES
from utils import store, verification

PORT = 8765

server = MCPServer(
    "fca-handbook",
    instructions=(
        "Search the FCA Handbook. Search results are candidates: read the provision with get_provision, follow "
        "expand_references for exceptions, and look up defined terms. Check every citation with check_citation. "
        "Only R (and UK/EU) provisions are binding rules; G is guidance. Never cite a result whose source is "
        "'fictional-sample' as FCA text."
    ),
)


def check_citation(reference: str, quote: str, as_of: str | None = None) -> dict[str, Any]:
    """Check, without a model, that a provision is in force and that a quote from it is word for word.

    Args:
        reference: The provision's reference, for example "SYSC 15A.2.1R".
        quote: The quoted words.
        as_of: Check against the rules in force on this date (YYYY-MM-DD). Leave out for today.
    """
    try:
        problems = verification.check_citation(reference, quote, date.fromisoformat(as_of) if as_of else None)
    except (store.IndexNotReady, ValueError) as error:
        problems = [f"could not check: {error}"]
    return {"ok": not problems, "problems": problems}


def index_status() -> dict[str, Any]:
    """Which Handbook snapshot is being searched, when it was taken, and which sourcebooks it covers."""
    try:
        return {"ready": True, **store.status()}
    except store.IndexNotReady as error:
        return {"ready": False, "problem": str(error)}


_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


def _fail_loudly(function):
    """A message such as "Error: no index" must not reach a caller as if it were Handbook text."""

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        result = function(*args, **kwargs)
        if result == NO_MATCHES:
            return []
        if isinstance(result, str):
            raise ToolError(result)
        return result

    return wrapper


for _function in [*(_fail_loudly(t.func) for t in HANDBOOK_TOOLS), check_citation, index_status]:
    server.add_tool(_function, description=inspect.getdoc(_function), annotations=_READ_ONLY)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stdio", action="store_true", help="serve one client over stdin and stdout")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    try:
        store.client()  # take the local folder now, so a second server fails at once with a clear message
    except RuntimeError as error:
        raise SystemExit(f"Cannot open the index: {error}") from None
    try:
        if args.stdio:
            server.run("stdio")
        else:
            server.run("streamable-http", host="127.0.0.1", port=args.port)
    except KeyboardInterrupt:
        pass
    finally:
        store.close()


if __name__ == "__main__":
    main()
