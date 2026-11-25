"""MCP server exposing FCA Handbook and Consumer Credit Act lookup tools to the compliance agent.

The tool names and argument keys are the defaults of `MCPToolMapping` in `adapters/regulatory/mcp.py`, so the agent's
`MCPFCAHandbookAdapter` and `MCPConsumerCreditActAdapter` connect with no configuration beyond the server location:

  search_fca_handbook(query, top_k, sourcebooks)       -> FCA Handbook passages, ranked by BM25
  get_fca_rule(reference)                              -> one FCA Handbook rule, e.g. 'CONC 3.5.7R'
  query_consumer_credit_act(query, top_k, sections)    -> CCA 1974 passages (best passage per section)
  get_consumer_credit_act_document(section)            -> one CCA section in full; with no section, a contents list

Every result is a passage with a `reference` you can cite, plus `corpus`, which tells you where it came from.
FCA Handbook content is the teammate's mock corpus, because the Handbook text is not in this repo yet, so those results
are tagged `"corpus": "mock"`. CCA content is the Act text in `cca.md`.

Run it:
  uv run fca-regulatory-mcp                      # stdio (LangGraph or any MCP client that spawns a subprocess)
  uv run fca-regulatory-mcp --http --port 8001   # Streamable HTTP at /mcp, for FCA_MCP_URL / CCA_MCP_URL
"""

from __future__ import annotations

import argparse
import functools
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ..adapters.regulatory.mock import FCA_CORPUS, MockFCAHandbookAdapter
from ..domain.models import RegulatoryPassage
from .bm25 import BM25
from .cca_corpus import CCAIndex, CCAPassage, CCASection, load_cca, normalise_section

CCA_URL = "https://www.legislation.gov.uk/ukpga/1974/39/section/{number}"

mcp = MCPServer(
    name="fca-cca-regulatory",
    instructions=(
        "Regulatory lookup for the FCA compliance agent. Use the FCA tools for FCA Handbook rules and the CCA tools for "
        "Consumer Credit Act 1974 provisions. Every result has a reference you can cite. An empty result means nothing "
        "in the corpus matched; it is not evidence that the rule does not apply."
    ),
)


class UnknownSectionError(ToolError, ValueError):
    """The Act has no such section. A ToolError, because MCP hides the message of any other exception from the caller."""


@functools.lru_cache(maxsize=1)
def _cca_index() -> CCAIndex:
    return CCAIndex(load_cca())


@functools.lru_cache(maxsize=1)
def _fca_index() -> BM25[RegulatoryPassage]:
    return BM25(FCA_CORPUS, fields=lambda p: [(p.reference, 2), (p.title, 3), (p.text, 1)])


@functools.lru_cache(maxsize=1)
def _fca_adapter() -> MockFCAHandbookAdapter:
    return MockFCAHandbookAdapter()


def _fca_dict(passage: RegulatoryPassage, score: float | None = None) -> dict[str, Any]:
    return {
        "reference": passage.reference,
        "title": passage.title,
        "text": passage.text,
        "url": passage.url,
        "score": None if score is None else round(score, 4),
        "corpus": "mock",
    }


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
async def search_fca_handbook(
    query: str,
    top_k: int = 5,
    sourcebooks: list[str] | None = None,
) -> dict[str, Any]:
    """Search FCA Handbook rules and guidance by keyword. Returns passages with canonical references such as CONC 3.5.7R.

    Use this to find rules that apply to a piece of code, a product spec, or a customer journey. `sourcebooks`
    optionally narrows the search to references that start with those prefixes, such as ['CONC'] or ['PRIN'].
    Returns no results if nothing matches.
    """
    prefixes = tuple(b.upper() for b in sourcebooks) if sourcebooks else None
    hits = _fca_index().search(
        query,
        limit=max(1, min(top_k, 20)),
        keep=(lambda p: p.reference.upper().startswith(prefixes)) if prefixes else None,
    )
    return {"results": [_fca_dict(p, score) for p, score in hits]}


@mcp.tool()
async def get_fca_rule(reference: str) -> dict[str, Any]:
    """Fetch one FCA Handbook rule by its canonical reference, for example 'CONC 4.2.5R' or 'PRIN 2A.2.1R'."""
    passage = await _fca_adapter().get_rule(reference)
    return {"results": [_fca_dict(passage)] if passage else []}


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
    parser = argparse.ArgumentParser(description="FCA Handbook & Consumer Credit Act MCP server")
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
