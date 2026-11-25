"""The Handbook RAG as an MCP server, for the compliance agent. Run it from this folder:

    uv run --no-sync python mcp_server.py            # HTTP on http://127.0.0.1:8765/mcp, shared by every agent call
    uv run --no-sync python mcp_server.py --stdio    # started by one MCP client, over stdin and stdout

Use HTTP for parallel reviews: on-disk Qdrant allows one process, and stdio clients may start several.
HTTP only listens on this machine. The server is read-only, and never loads FCA credentials.
"""

import argparse
import functools
import inspect

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from tools.handbook import HANDBOOK_TOOLS
from utils import store

PORT = 8765

server = MCPServer(
    "fca-handbook",
    instructions=(
        "The FCA Handbook. Search widely with search_fca_handbook, read the whole section with get_fca_section before "
        "judging, and check every quote with check_fca_citation. Never present a result whose source is "
        "'fictional-sample' as FCA text."
    ),
)


def _fail_loudly(function):
    """Report a failure as an MCP error, with a message the caller can act on."""

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (store.IndexNotReady, ValueError) as error:
            raise ToolError(str(error)) from error

    return wrapper


_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
for _function in HANDBOOK_TOOLS:
    server.add_tool(_fail_loudly(_function), description=inspect.getdoc(_function), annotations=_READ_ONLY)


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
