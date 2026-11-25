"""Tools that let an agent search the FCA Handbook index."""

from datetime import date

from langchain.tools import tool

from utils import store
from utils.references import parse_reference

_FIELDS = (
    "citation", "provision_type", "heading_path", "text", "effective_from", "effective_to", "url", "snapshot_date",
    "source", "attribution",
)  # fmt: skip


def _public(payload: dict) -> dict:
    return {field: payload[field] for field in _FIELDS}


def _date(as_of: str | None) -> date:
    return date.fromisoformat(as_of) if as_of else date.today()


@tool(parse_docstring=True)
def search_handbook(
    query: str,
    sourcebooks: list[str] | None = None,
    provision_types: list[str] | None = None,
    as_of: str | None = None,
    limit: int = 8,
) -> list[dict] | str:
    """Search the FCA Handbook by meaning and by exact words. Returns the provisions in force, best match first.

    Results are candidates, not findings: read the whole provision with get_provision before relying on one. A long
    provision may be shown in part.

    Args:
        query: What to look for, for example "change management testing before deployment" or "SYSC 15A.2".
        sourcebooks: Only these sourcebooks, for example ["SYSC", "PRIN"]. Leave out to search all of them.
        provision_types: Only these types, for example ["R"]. R is a binding rule and G is guidance.
        as_of: Search the rules in force on this date (YYYY-MM-DD). Leave out for today.
        limit: How many provisions to return, from 1 to 20.
    """
    try:
        rows = store.search(query, _date(as_of), sourcebooks, provision_types, max(1, min(limit, 20)))
    except (store.IndexNotReady, ValueError) as error:
        return f"Error: {error}"
    return [_public(row) for row in rows] or "No provisions matched."


@tool(parse_docstring=True)
def get_provision(reference: str, as_of: str | None = None) -> dict | str:
    """Get the full text of one Handbook provision by its reference.

    Args:
        reference: The provision's reference, for example "SYSC 15A.2.1R".
        as_of: Get the version in force on this date (YYYY-MM-DD). Leave out for today.
    """
    try:
        row = store.get(reference, _date(as_of))
    except (store.IndexNotReady, ValueError) as error:
        return f"Error: {error}"
    return _public(row) if row else f"{reference} is not in force in the index. Do not cite it."


@tool(parse_docstring=True)
def lookup_glossary(term: str) -> list[dict] | str:
    """Look up a term the Handbook defines. Defined terms can mean something narrower than everyday English.

    Args:
        term: The defined term, for example "important business service".
    """
    try:
        rows = store.glossary(term)
    except store.IndexNotReady as error:
        return f"Error: {error}"
    return [{k: r[k] for k in ("term", "definition", "source")} for r in rows] or f"{term!r} is not a defined term."


@tool(parse_docstring=True)
def expand_references(reference: str, as_of: str | None = None) -> dict | str:
    """Find the provisions a provision cites, with their text, and list the provisions that cite it.

    Use it to find exceptions, conditions and guidance that sit apart from the rule itself.

    Args:
        reference: The provision's reference, for example "SYSC 15A.2.1R".
        as_of: Use the versions in force on this date (YYYY-MM-DD). Leave out for today.
    """
    if parse_reference(reference) is None:
        return f"{reference} is not a Handbook reference."
    try:
        day = _date(as_of)
        row = store.get(reference, day)
        if row is None:
            return f"{reference} is not in force in the index. Do not cite it."
        found = {ref: store.get(ref, day) for ref in row["cites"]}
        return {
            "citation": row["citation"],
            "cites": list({p["citation"]: _public(p) for p in found.values() if p}.values()),
            "cited_but_not_in_index": [ref for ref, p in found.items() if p is None],
            "cited_by": store.cited_by(row["key"], day),
        }
    except (store.IndexNotReady, ValueError) as error:
        return f"Error: {error}"


HANDBOOK_TOOLS = [search_handbook, get_provision, lookup_glossary, expand_references]
