"""Tests for the regulatory MCP server, including an end-to-end run through the agent's MCP adapters over stdio."""

from __future__ import annotations

import sys

import pytest

from fca_compliance_agent.mcp_server import server as server_module
from fca_compliance_agent.mcp_server.cca_corpus import CCAIndex, load_cca, normalise_section


@pytest.fixture(scope="module")
def cca_sections():
    return load_cca()


@pytest.fixture(scope="module")
def cca_index(cca_sections):
    return CCAIndex(cca_sections)


def test_parser_finds_operative_sections(cca_sections, cca_index):
    numbers = {s.number for s in cca_sections}
    for number in ["20", "55", "55A", "55C", "61", "66A", "68", "69", "77", "86B", "87", "88", "94", "95", "140A", "193"]:
        assert number in numbers, f"section {number} missing"
    assert cca_index.get("66A").title == "Withdrawal from consumer credit agreement"
    assert "14 days" in cca_index.get("66A").text
    assert cca_index.get("55A").text.startswith("[No operative text")


def test_parser_strips_footnotes_and_page_furniture(cca_index):
    for section in cca_index.sections:
        assert "Consumer Credit Act 1974 (c. 39)" not in section.text
        assert "Textual Amendments" not in section.text
        assert "Document Generated" not in section.text


def test_marked_heading_resolves_section_number(cca_index):
    # '[F4255C Copy of draft ...' is section 55C. A naive split would read it as 5C.
    assert cca_index.get("55C").title == "Copy of draft consumer credit agreement"
    assert cca_index.get("5C") is None


def test_normalise_section_accepts_common_forms():
    assert normalise_section("s.55") == "55"
    assert normalise_section("Section 66a") == "66A"
    assert normalise_section(" 140A ") == "140A"


def test_search_ranks_relevant_sections(cca_index):
    top = [p.number for p, _ in cca_index.search("withdraw within 14 days", limit=3)]
    assert "66A" in top
    top = [p.number for p, _ in cca_index.search("early settlement rebate", limit=3)]
    assert top[0] == "95"


def test_stemming_matches_inflected_forms(cca_index):
    # 'withdrawal' in the query should find s.66A, whose text says 'withdraw'.
    top = [p.number for p, _ in cca_index.search("withdrawal", limit=3)]
    assert "66A" in top


def test_search_returns_one_passage_per_section(cca_index):
    hits = cca_index.search("debtor creditor agreement default", limit=10)
    numbers = [p.number for p, _ in hits]
    assert len(numbers) == len(set(numbers))


def test_passages_are_bounded_in_size(cca_index):
    assert max(len(p.text) for p in cca_index.passages) <= 1300
    long_section = cca_index.get("86B")
    parts = [p for p in cca_index.passages if p.number == "86B"]
    assert len(parts) > 1 and parts[0].parts == len(parts)
    assert "".join(p.text for p in parts).replace("\n", " ").startswith(long_section.text[:40])


def test_search_can_be_restricted_to_sections(cca_index):
    hits = cca_index.search("withdraw", limit=5, numbers={"55", "69"})
    assert {p.number for p, _ in hits} <= {"55", "69"}


def test_fca_search_filters_by_sourcebook_and_returns_nothing_for_no_match():
    import asyncio

    prin = asyncio.run(server_module.search_fca_handbook("consumer duty good outcomes", sourcebooks=["PRIN"]))
    assert prin["results"] and all(r["reference"].startswith("PRIN") for r in prin["results"])
    nothing = asyncio.run(server_module.search_fca_handbook("quantum cryptography"))
    assert nothing == {"results": []}


def test_tool_search_fca_returns_mock_handbook_passages():
    import asyncio

    result = asyncio.run(server_module.search_fca_handbook("affordability creditworthiness", top_k=3))
    refs = [r["reference"] for r in result["results"]]
    assert "CONC 5.2A.4R" in refs
    assert all(r["corpus"] == "mock" for r in result["results"])


def test_tool_get_fca_rule_unknown_reference_returns_empty():
    import asyncio

    assert asyncio.run(server_module.get_fca_rule("NOT A RULE")) == {"results": []}


def test_tool_get_consumer_credit_act_document_contents_and_section():
    contents = server_module.get_consumer_credit_act_document()["results"]
    assert len(contents) > 200
    section = server_module.get_consumer_credit_act_document("s.66A")["results"][0]
    assert section["reference"] == "CCA 1974 s.66A"
    assert "14 days" in section["text"]


def test_tool_unknown_cca_section_raises():
    with pytest.raises(ValueError, match="no section 999"):
        server_module.get_consumer_credit_act_document("999")


def test_stdio_end_to_end_through_agent_adapters():
    """The agent's own MCP adapters spawn this server over stdio and parse what it returns."""
    pytest.importorskip("mcp")
    import asyncio

    from fca_compliance_agent.adapters.regulatory.mcp import (
        MCPConsumerCreditActAdapter,
        MCPFCAHandbookAdapter,
        MCPServerConfig,
    )

    config = MCPServerConfig(
        transport="stdio",
        command=sys.executable,
        args=["-m", "fca_compliance_agent.mcp_server.server"],
    )

    async def run() -> None:
        fca = MCPFCAHandbookAdapter(server_config=config)
        cca = MCPConsumerCreditActAdapter(server_config=config)

        fca_hits = await fca.search_rules("promotion representative APR", top_k=3)
        assert fca_hits and all(p.source == "fca_handbook" for p in fca_hits)
        assert any(p.reference == "CONC 3.5.7R" for p in fca_hits)

        rule = await fca.get_rule("PRIN 2A.2.1R")
        assert rule is not None and "good outcomes" in rule.text

        cca_hits = await cca.query_provisions("withdraw within 14 days", top_k=3)
        assert cca_hits and all(p.source == "consumer_credit_act" for p in cca_hits)
        assert any(p.reference == "CCA 1974 s.66A" for p in cca_hits)

        section = await cca.get_document(section="66A")
        assert section and section[0].reference == "CCA 1974 s.66A"
        assert section[0].url and section[0].url.endswith("/section/66A")

    asyncio.run(run())
