"""Tests for the regulatory MCP server.

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
    def test_fca_search_filters_by_sourcebook_and_returns_nothing_for_no_match(self) -> None:
        prin = asyncio.run(server_module.search_fca_handbook("consumer duty good outcomes", sourcebooks=["PRIN"]))
        self.assertTrue(prin["results"])
        self.assertTrue(all(r["reference"].startswith("PRIN") for r in prin["results"]))
        nothing = asyncio.run(server_module.search_fca_handbook("quantum cryptography"))
        self.assertEqual(nothing, {"results": []})

    def test_fca_search_returns_mock_handbook_passages(self) -> None:
        result = asyncio.run(server_module.search_fca_handbook("affordability creditworthiness", top_k=3))
        refs = [r["reference"] for r in result["results"]]
        self.assertIn("CONC 5.2A.4R", refs)
        self.assertTrue(all(r["corpus"] == "mock" for r in result["results"]))

    def test_get_fca_rule_unknown_reference_returns_empty(self) -> None:
        self.assertEqual(asyncio.run(server_module.get_fca_rule("NOT A RULE")), {"results": []})

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
    def test_agent_adapters_talk_to_server_over_stdio(self) -> None:
        """The agent's own MCP adapters spawn this server over stdio and parse what it returns."""
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
            self.assertTrue(fca_hits)
            self.assertTrue(all(p.source == "fca_handbook" for p in fca_hits))
            self.assertIn("CONC 3.5.7R", [p.reference for p in fca_hits])

            rule = await fca.get_rule("PRIN 2A.2.1R")
            self.assertIsNotNone(rule)
            self.assertIn("good outcomes", rule.text)

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
