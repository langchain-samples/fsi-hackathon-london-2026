"""Tests for the Consumer Credit Act 1974 regulatory MCP server.

Written with `unittest` so they run in CI with `python -m unittest discover`, which installs no test runner. The
server tests need the `mcp` extra and are skipped when it is not installed. The CCA parser and index tests need only
the standard library and always run.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest

from fca_compliance_agent.mcp_server.cca_corpus import CCAIndex, load_cca, normalise_section

MCP_INSTALLED = importlib.util.find_spec("mcp") is not None

if MCP_INSTALLED:
    from fca_compliance_agent.mcp_server import server as server_module


class CCACorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sections = load_cca()
        cls.index = CCAIndex(cls.sections)

    def test_parser_finds_operative_sections(self) -> None:
        numbers = {s.number for s in self.sections}
        for number in ["20", "55", "55A", "55C", "61", "66A", "68", "69", "77", "86B", "87", "88", "94", "95", "140A", "193"]:
            self.assertIn(number, numbers)
        self.assertEqual(self.index.get("66A").title, "Withdrawal from consumer credit agreement")
        self.assertIn("14 days", self.index.get("66A").text)
        self.assertTrue(self.index.get("55A").text.startswith("[No operative text"))

    def test_parser_strips_footnotes_and_page_furniture(self) -> None:
        for section in self.index.sections:
            self.assertNotIn("Consumer Credit Act 1974 (c. 39)", section.text)
            self.assertNotIn("Textual Amendments", section.text)
            self.assertNotIn("Document Generated", section.text)

    def test_marked_heading_resolves_section_number(self) -> None:
        # '[F4255C Copy of draft ...' is section 55C. A naive split would read it as 5C.
        self.assertEqual(self.index.get("55C").title, "Copy of draft consumer credit agreement")
        self.assertIsNone(self.index.get("5C"))

    def test_normalise_section_accepts_common_forms(self) -> None:
        self.assertEqual(normalise_section("s.55"), "55")
        self.assertEqual(normalise_section("Section 66a"), "66A")
        self.assertEqual(normalise_section(" 140A "), "140A")

    def test_search_ranks_relevant_sections(self) -> None:
        top = [p.number for p, _ in self.index.search("withdraw within 14 days", limit=3)]
        self.assertIn("66A", top)
        top = [p.number for p, _ in self.index.search("early settlement rebate", limit=3)]
        self.assertEqual(top[0], "95")

    def test_stemming_matches_inflected_forms(self) -> None:
        # 'withdrawal' in the query should find s.66A, whose text says 'withdraw'.
        top = [p.number for p, _ in self.index.search("withdrawal", limit=3)]
        self.assertIn("66A", top)

    def test_search_returns_one_passage_per_section(self) -> None:
        numbers = [p.number for p, _ in self.index.search("debtor creditor agreement default", limit=10)]
        self.assertEqual(len(numbers), len(set(numbers)))

    def test_passages_are_bounded_in_size(self) -> None:
        self.assertLessEqual(max(len(p.text) for p in self.index.passages), 1300)
        parts = [p for p in self.index.passages if p.number == "86B"]
        self.assertGreater(len(parts), 1)
        self.assertEqual(parts[0].parts, len(parts))

    def test_search_can_be_restricted_to_sections(self) -> None:
        hits = self.index.search("withdraw", limit=5, numbers={"55", "69"})
        self.assertTrue({p.number for p, _ in hits} <= {"55", "69"})


@unittest.skipUnless(MCP_INSTALLED, "needs the mcp extra: uv sync --extra mcp")
class ServerToolTests(unittest.TestCase):
    def test_server_exposes_only_real_cca_tools(self) -> None:
        self.assertFalse(hasattr(server_module, "search_fca_handbook"))
        self.assertFalse(hasattr(server_module, "get_fca_rule"))
        self.assertTrue(hasattr(server_module, "query_consumer_credit_act"))
        self.assertTrue(hasattr(server_module, "get_consumer_credit_act_document"))

    def test_query_consumer_credit_act_returns_real_statute_passages(self) -> None:
        result = server_module.query_consumer_credit_act("withdraw within 14 days", top_k=3)
        refs = [r["reference"] for r in result["results"]]
        self.assertIn("CCA 1974 s.66A", refs)
        self.assertTrue(all(r["corpus"] == "consumer_credit_act_1974" for r in result["results"]))

    def test_get_consumer_credit_act_document_contents_and_section(self) -> None:
        contents = server_module.get_consumer_credit_act_document()["results"]
        self.assertGreater(len(contents), 200)
        section = server_module.get_consumer_credit_act_document("s.66A")["results"][0]
        self.assertEqual(section["reference"], "CCA 1974 s.66A")
        self.assertIn("14 days", section["text"])

    def test_unknown_cca_section_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "no section 999"):
            server_module.get_consumer_credit_act_document("999")


@unittest.skipUnless(MCP_INSTALLED, "needs the mcp extra: uv sync --extra mcp")
class StdioEndToEndTests(unittest.TestCase):
    def test_agent_cca_adapter_talks_to_server_over_stdio(self) -> None:
        """The agent's MCPConsumerCreditActAdapter spawns this server over stdio and parses what it returns."""
        from fca_compliance_agent.adapters.regulatory.mcp import (
            MCPConsumerCreditActAdapter,
            MCPServerConfig,
        )

        config = MCPServerConfig(
            transport="stdio",
            command=sys.executable,
            args=("-m", "fca_compliance_agent.mcp_server.server"),
        )

        async def run() -> None:
            cca = MCPConsumerCreditActAdapter(server_config=config)

            cca_hits = await cca.query_provisions("withdraw within 14 days", top_k=3)
            self.assertTrue(all(p.source == "consumer_credit_act" for p in cca_hits))
            self.assertIn("CCA 1974 s.66A", [p.reference for p in cca_hits])

            section = await cca.get_document(section="66A")
            self.assertEqual(section[0].reference, "CCA 1974 s.66A")
            self.assertTrue(section[0].url.endswith("/section/66A"))
            self.assertEqual(await cca.get_document(section="999"), [])  # the server says it does not exist

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
