"""The MCP server, called by the core agent's own adapter: in-process, over HTTP and over stdio."""

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters
from mcp_server import server
from tests.conftest import SAMPLE
from utils import store
from utils.schema import Provision, Snapshot

ROOT = Path(__file__).resolve().parent.parent
LONG_RULE = "A firm must keep careful records of every decision. " * 20 + "Except on Sundays."
NAMES = {"search_fca_handbook", "get_fca_section", "check_fca_citation"}


async def _call(target, name, arguments):
    async with Client(target) as client:
        return await client.call_tool(name, arguments)


def call(target, name, **arguments):
    return asyncio.run(_call(target, name, arguments))


async def _list(target):
    async with Client(target) as client:
        return (await client.list_tools()).tools


def test_exactly_three_read_only_tools(index):
    tools = asyncio.run(_list(server))
    assert {t.name for t in tools} == NAMES
    assert all(t.annotations.read_only_hint and len(t.description) > 100 for t in tools)


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("get_fca_rule", {"reference": "XSYS 4.1.1R"}),  # not a tool of this server
        ("search_fca_handbook", {"query": "x", "as_of": "soon"}),
        ("search_fca_handbook", {"query": "x", "top_k": "many"}),
        ("get_fca_section", {"reference": "XSYS 99.9.9R"}),
    ],
)
def test_failures_are_mcp_errors(index, name, arguments):
    assert call(server, name, **arguments).is_error


def test_a_missing_index_is_an_mcp_error(empty_index):
    result = call(server, "search_fca_handbook", query="x")
    assert result.is_error and "no Handbook index" in result.content[0].text


@pytest.fixture
def http_server(empty_index):
    snapshot = Snapshot.load(SAMPLE)
    snapshot.provisions.append(Provision(ref="XBIG 1.1.1R", provision_type="R", text=LONG_RULE))
    store.build_index(snapshot, "handbook_sample")
    store.close()  # hand the folder over to the server process
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    process = subprocess.Popen([sys.executable, "mcp_server.py", "--port", str(port)], cwd=ROOT, env=os.environ.copy(),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
    for _ in range(100):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        assert process.poll() is None, "the server stopped"
        time.sleep(0.1)
    yield f"http://127.0.0.1:{port}/mcp"
    process.terminate()
    process.wait(10)


def test_the_core_agents_adapter_works_with_this_server(http_server):
    from fca_compliance_agent import MCPFCAHandbookAdapter, MCPServerConfig, MCPToolError

    adapter = MCPFCAHandbookAdapter(server_config=MCPServerConfig(url=http_server))

    async def run():
        found = await adapter.search_rules("XSYS 4.2.1R", top_k=3)
        assert found[0].reference == "XSYS 4.2.1R" and found[0].metadata["source"] == "fictional-sample"
        assert await adapter.search_rules("testing", sourcebooks=["CONC", "PRIN 2A"]) == []
        assert (await adapter.search_rules_in_scope("testing", sourcebooks=["CONC"])).not_in_index == ["CONC"]
        assert (await adapter.get_rule("XSYS 4.2.1R")).reference == "XSYS 4.2.1R"
        assert await adapter.get_rule("XSYS 4.2.9R") is None
        # The fixed review step judges on search_rules, so it must get whole rules and not snippets.
        long = await adapter.search_rules("XBIG 1.1.1R", top_k=1)
        assert long[0].text == LONG_RULE
        assert (await adapter.search("XBIG 1.1.1R", top_k=1)).passages[0].text.endswith("[...]")
        section = await adapter.get_section("XSYS 4.1.2G")
        assert [p.reference for p in section.passages] == ["XSYS 4.1.1R", "XSYS 4.1.2G", "XSYS 4.1.3R"]
        assert section.complete and "emergency change" in section.definitions and section.links == ["XSYS 4.2.1R"]
        good = await adapter.check_citation("XSYS 4.2.1R", "approved by a person other than")
        assert good.ok and good.binding and good.fictional
        assert not (await adapter.check_citation("XSYS 4.2.1R", "approved by absolutely nobody else")).ok
        with pytest.raises(MCPToolError, match="not in force"):
            await adapter.get_section("XSYS 99.9.9R")
        many = await asyncio.gather(*(adapter.search_rules("audit logs") for _ in range(24)))
        assert len({tuple(p.reference for p in found) for found in many}) == 1

    asyncio.run(run())


def test_second_server_is_refused_clearly(http_server):
    second = subprocess.run([sys.executable, "mcp_server.py", "--stdio"], cwd=ROOT, env=os.environ.copy(),
                            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)  # fmt: skip
    assert second.returncode != 0 and "Cannot open the index" in second.stderr


def test_stdio(index):
    store.close()
    parameters = StdioServerParameters(command=sys.executable, args=["mcp_server.py", "--stdio"], cwd=str(ROOT),
                                       env=os.environ.copy())  # fmt: skip
    assert not call(parameters, "get_fca_section", reference="XSYS 4.1.1R").is_error
