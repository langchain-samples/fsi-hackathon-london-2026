"""MCP server exposing Consumer Credit Act 1974 lookup tools to the compliance agent.

The tool names and argument keys are the defaults of `MCPToolMapping` in `adapters/regulatory/mcp.py`,
so `MCPConsumerCreditActAdapter` connects with no configuration beyond the server location:

  query_consumer_credit_act(query, top_k, sections)    -> CCA 1974 passages (best passage per section)
  get_consumer_credit_act_document(section)            -> one CCA section in full; with no section, a contents list

Every result is grounded in the statutory Act text in `cca.md` and tagged
`"corpus": "consumer_credit_act_1974"`. The real FCA Handbook RAG is served separately by
`04_fca_compliance_agent/handbook_rag/mcp_server.py`.

Run it:
  uv run fca-regulatory-mcp                      # stdio (LangGraph or any MCP client that spawns a subprocess)
  uv run fca-regulatory-mcp --http --port 8001   # Streamable HTTP at /mcp, for CCA_MCP_URL
"""

from __future__ import annotations

import argparse
import functools
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .cca_corpus import CCAIndex, CCAPassage, CCASection, load_cca, normalise_section

CCA_URL = "https://www.legislation.gov.uk/ukpga/1974/39/section/{number}"

mcp = MCPServer(
    name="cca-1974-regulatory",
    instructions=(
        "Consumer Credit Act 1974 regulatory lookup for the FCA compliance agent. Every result has a "
        "canonical reference you can cite. An empty result means nothing in the Act text matched; it "
        "is not evidence that the Act does not apply."
    ),
)


class UnknownSectionError(ToolError, ValueError):
    """The Act has no such section. A ToolError, because MCP hides the message of any other exception from the caller."""


@functools.lru_cache(maxsize=1)
def _cca_index() -> CCAIndex:
    return CCAIndex(load_cca())


def _cca_passage_dict(passage: CCAPassage, score: float | None = None) -> dict[str, Any]:
    return {
        "reference": passage.reference,
        "title": passage.title,
        "text": passage.text,
        "url": CCA_URL.format(number=passage.number),
        "score": None if score is None else round(score, 4),
        "corpus": "consumer_credit_act_1974",
        "part": f"{passage.part} of {passage.parts}",
    }


def _cca_section_dict(section: CCASection) -> dict[str, Any]:
    return {
        "reference": section.reference,
        "title": section.title,
        "text": section.text,
        "url": CCA_URL.format(number=section.number),
        "score": None,
        "corpus": "consumer_credit_act_1974",
    }


def _section_numbers(sections: list[str] | None) -> set[str] | None:
    if not sections:
        return None
    return {normalise_section(s) for s in sections}


@mcp.tool()
def query_consumer_credit_act(
    query: str,
    top_k: int = 5,
    sections: list[str] | None = None,
) -> dict[str, Any]:
    """Search the Consumer Credit Act 1974 by topic, for example 'withdrawal within 14 days' or 'default notice'.

    Returns the best-matching passage from each section, with its section reference (such as 'CCA 1974 s.66A') and a
    `part` field showing where it sits in the section. Use get_consumer_credit_act_document to read a whole section.
    `sections` optionally restricts the search to given section numbers, such as ['55', '66A'].
    """
    hits = _cca_index().search(query, limit=max(1, min(top_k, 20)), numbers=_section_numbers(sections))
    return {"results": [_cca_passage_dict(p, score) for p, score in hits]}


@mcp.tool()
def get_consumer_credit_act_document(section: str | None = None) -> dict[str, Any]:
    """Return one Consumer Credit Act section in full, such as '55', '66A' or 's.140A'.

    With no section, returns the list of sections (reference and title only), so you can choose one to fetch.
    Raises an error if the section does not exist in the Act text.
    """
    index = _cca_index()
    if section is None:
        return {
            "results": [
                {"reference": s.reference, "title": s.title, "text": s.title, "corpus": "consumer_credit_act_1974"}
                for s in index.sections
            ]
        }
    found = index.get(section)
    if found is None:
        raise UnknownSectionError(
            f"CCA 1974 has no section {normalise_section(section)}. "
            "Check the number, or search with query_consumer_credit_act."
        )
    return {"results": [_cca_section_dict(found)]}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Consumer Credit Act 1974 MCP server")
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args(argv)

    if args.http:
        mcp.run("streamable-http", host=args.host, port=args.port)
    else:
        mcp.run("stdio")


if __name__ == "__main__":
    main()
