"""The MCP server, called through a real MCP client. In-process, over HTTP, and over stdio."""

import os
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from mcp import StdioServerParameters
from mcp_client import call_handbook, load_handbook_tools
from mcp_server import server
from utils import store

ROOT = Path(__file__).resolve().parent.parent
TODAY = "2026-11-25"
NAMES = {"search_handbook", "get_provision", "lookup_glossary", "expand_references", "check_citation", "index_status"}


def test_tools_are_listed_with_descriptions(index):
    tools = load_handbook_tools(server)
    assert {t.name for t in tools} == NAMES
    assert all("\n" in t.description or len(t.description) > 40 for t in tools)
    search = next(t for t in tools if t.name == "search_handbook")
    assert set(search.args) == {"query", "sourcebooks", "provision_types", "as_of", "limit"}


def test_check_citation_and_status(index):
    good = {"reference": "XSYS 4.2.1R", "quote": "approved by a person other than", "as_of": TODAY}
    assert call_handbook("check_citation", good, server) == {"ok": True, "problems": []}
    assert call_handbook("check_citation", {**good, "quote": "made up"}, server)["ok"] is False
    assert call_handbook("check_citation", {**good, "as_of": "soon"}, server)["ok"] is False
    status = call_handbook("index_status", {}, server)
    assert (status["provisions"], status["sourcebooks"]) == (13, ["XPRN", "XSYS"])


def test_no_index_is_reported(empty_index):
    assert call_handbook("index_status", {}, server)["ready"] is False


def test_bad_arguments_become_a_message_for_the_model(index):
    search = next(t for t in load_handbook_tools(server) if t.name == "search_handbook")
    message = {"name": search.name, "args": {"query": "x", "limit": "many"}, "id": "1", "type": "tool_call"}
    assert search.invoke(message).status == "error"


@pytest.fixture
def http_server(index):
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


def test_http_with_parallel_sub_agents(http_server):
    search = next(t for t in load_handbook_tools(http_server) if t.name == "search_handbook")
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda _: search.invoke({"query": "XSYS 4.2.1R", "as_of": TODAY}), range(24)))
    assert "XSYS 4.2.1R" in results[0] and len(set(results)) == 1


async def _in_event_loop(url):
    return load_handbook_tools(url), await load_handbook_tools(url)[0].ainvoke({"query": "audit logs", "as_of": TODAY})


def test_http_inside_an_event_loop(http_server):
    import asyncio

    tools, result = asyncio.run(_in_event_loop(http_server))
    assert len(tools) == len(NAMES) and "XSYS 5.1.1R" in result


def test_second_server_is_refused_clearly(http_server):
    second = subprocess.run([sys.executable, "mcp_server.py", "--stdio"], cwd=ROOT, env=os.environ.copy(),
                            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)  # fmt: skip
    assert second.returncode != 0 and "Cannot open the index" in second.stderr


def test_stdio(index):
    store.close()
    parameters = StdioServerParameters(command=sys.executable, args=["mcp_server.py", "--stdio"], cwd=str(ROOT),
                                       env=os.environ.copy())  # fmt: skip
    assert call_handbook("index_status", {}, parameters)["source"] == "fictional-sample"
