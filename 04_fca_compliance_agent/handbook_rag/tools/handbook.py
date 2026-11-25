"""The Handbook tools: search widely, then read the whole section, then check the quote.

Failures raise, and never come back as text, so that an error cannot be mistaken for a rule.
"""

from datetime import date
from typing import Any

from utils import store, verification
from utils.references import Reference, parse_reference, section_of

SNIPPET = 300
SECTION_LIMIT = 12_000  # characters, about 3,000 tokens
PROVISION_LIMIT = 40_000  # only a few very long annexes and tables are longer


def _date(as_of: str | None) -> date:
    return date.fromisoformat(as_of) if as_of else date.today()


def _describe(row: dict, text: str) -> dict[str, Any]:
    warning = ["FICTIONAL SAMPLE, NOT FCA TEXT"] if row["source"] == "fictional-sample" else []
    return {
        "reference": row["citation"],
        "title": " > ".join([*warning, *row["heading_path"], *([row["subheading"]] if row["subheading"] else [])]),
        "text": text,
        "provision_type": row["provision_type"],
        "binding": store.is_binding(row["provision_type"]),
        "section": row["section"],
        "effective_from": row["effective_from"],
        "effective_to": row["effective_to"],
        "url": row["url"],
        "snapshot_date": row["snapshot_date"],
        "source": row["source"],
    }


def _scope(sourcebooks: list[str] | None) -> tuple[list[str], list[str]]:
    """Split the requested scope into what the index has and what it lacks. "PRIN 2A" means just that chapter, and
    "CONC 5.2A" just that section."""
    wanted = [" ".join(s.upper().split()) for s in sourcebooks or [] if s.strip()]
    if not wanted:
        return [], []
    available = store.scopes()
    return [s for s in wanted if s in available], [s for s in wanted if s not in available]


def search_fca_handbook(
    query: str, top_k: int = 8, sourcebooks: list[str] | None = None, as_of: str | None = None
) -> dict[str, Any]:
    """Find FCA Handbook provisions that might matter. Searches by meaning, exact words and rule reference.

    Returns a short list: reference, heading, type and a snippet. These are candidates, not findings. Read the section
    with get_fca_section before relying on one. An empty list does not mean that no rule applies.

    Args:
        query: One topic in regulatory words, for example "testing changes before release", or a reference.
        top_k: How many provisions to return, from 1 to 20.
        sourcebooks: Only these sourcebooks, chapters or sections, for example ["CONC", "PRIN 2A", "CONC 5.2A"]. Leave
            out to search all.
        as_of: Use the rules in force on this date (YYYY-MM-DD). Leave out for today.
    """
    day = _date(as_of)
    scope, missing = _scope(sourcebooks)
    rows = [] if missing and not scope else store.search(query, day, scope or None, max(1, min(top_k, 20)))
    results = []
    for row in rows:
        results.append(_describe(row, _snippet(row)))
    return {"results": results, "not_in_index": missing}


def _snippet(row: dict) -> str:
    """The start of what matched. It ends in [...] unless it is the whole provision: a match may be one part of a long one."""
    text = " ".join(row["text"].split())
    if len(text) > SNIPPET:
        return text[:SNIPPET].rsplit(" ", 1)[0] + " [...]"
    return text if row["text"] == row["full_text"] else text + " [...]"


def _size(rows: list[dict]) -> int:
    return sum(len(r["text"]) for r in rows)


def _around(rows: list[dict], anchor: int, limit: int) -> list[dict]:
    """The anchor, and as many whole neighbours on each side as fit. A provision is never cut."""
    low = high = anchor
    while True:
        grew = False
        for step in (-1, 1):
            new = low - 1 if step < 0 else high + 1
            if 0 <= new < len(rows) and _size(rows[min(new, low) : max(new, high) + 1]) <= limit:
                low, high, grew = min(new, low), max(new, high), True
        if not grew:
            return rows[low : high + 1]


def _select(rows: list[dict], anchor: int, limit: int) -> tuple[list[dict], str]:
    if _size(rows) <= limit:
        return rows, "full"
    if any(r["subheading"] for r in rows):
        low = high = anchor  # the unbroken run of provisions under the anchor's sub-heading
        while low > 0 and rows[low - 1]["subheading"] == rows[anchor]["subheading"]:
            low -= 1
        while high + 1 < len(rows) and rows[high + 1]["subheading"] == rows[anchor]["subheading"]:
            high += 1
        group = rows[low : high + 1]
        if _size(group) <= limit:
            return group, "group"
        return _around(group, anchor - low, limit), "neighbours"
    return _around(rows, anchor, limit), "neighbours"


def get_fca_section(reference: str, as_of: str | None = None) -> dict[str, Any]:
    """Read a whole FCA Handbook section: every rule and guidance paragraph, in order, so exceptions are not missed.

    A section too long to return is cut down, around the provision you asked for, to the group under its sub-heading, or
    to its neighbours. Then `complete` is false and `outline` lists what was left out: call again with a reference from
    the outline to read it. Provisions are not cut part-way, except for a few annexes longer than 40,000 characters.

    Args:
        reference: A provision such as "SYSC 15A.2.1R", or a section such as "SYSC 15A.2".
        as_of: Use the rules in force on this date (YYYY-MM-DD). Leave out for today.
    """
    parsed = parse_reference(reference)
    if parsed is None:
        raise ValueError(f"{reference!r} is not a Handbook reference")
    day = _date(as_of)
    row = store.get(reference, day)
    section = row["section"] if row else _section_named(parsed, day)
    if section is None:
        raise ValueError(f"{reference} is not in force in the index. Do not cite it.{_suggest(parsed, day)}")
    rows = store.get_section(section, day)
    anchor = next((i for i, r in enumerate(rows) if row and r["citation"] == row["citation"]), 0)
    chosen, mode = _select(rows, anchor, SECTION_LIMIT)
    shown = {r["citation"] for r in chosen}
    text = "\n".join(r["text"] for r in chosen)
    return {
        "section": section,
        "complete": len(chosen) == len(rows) and all(len(r["text"]) <= PROVISION_LIMIT for r in chosen),
        "mode": mode,
        "results": [_describe(r, _capped(r["text"])) for r in chosen],
        "outline": [
            f"{r['citation']}: {(r['subheading'] + ': ') if r['subheading'] else ''}{' '.join(r['text'].split())[:80]}"
            for r in rows
            if r["citation"] not in shown
        ],
        "definitions": store.definitions(text),
        "links": [ref for ref in dict.fromkeys(x for r in chosen for x in r["cites"]) if ref not in shown][:20],
    }


def _capped(text: str) -> str:
    if len(text) <= PROVISION_LIMIT:
        return text
    return f"{text[:PROVISION_LIMIT].rsplit(chr(10), 1)[0]}\n[...] {len(text) - PROVISION_LIMIT:,} more characters: see the url"


def _section_named(parsed: Reference, day: date) -> str | None:
    """The section a reference names, if it names one. "COBS 6.1G" is a section of its own, and not COBS 6.1."""
    exact = parsed.candidate_keys()[-1]
    if section_of(exact) == exact and store.get_section(exact, day):
        return exact
    # An annex may be named with the type of its provisions: "SYSC 22 Annex 1R" for "SYSC 22 Annex 1.1R" and so on.
    if parsed.type and " ANNEX " in parsed.key and section_of(parsed.key) == parsed.key:
        if any(r["provision_type"] == parsed.type for r in store.get_section(parsed.key, day)):
            return parsed.key
    return None


def _suggest(parsed: Reference, day: date) -> str:
    """A chapter such as "SYSC 15A" has no text of its own, so list its sections. "CONC 5C" is not CONC 5."""
    for key in reversed(parsed.candidate_keys()):
        if options := store.sections(key, day):
            return f" Sections in {key}: {', '.join(options)}."
    return ""


def check_fca_citation(reference: str, quote: str, as_of: str | None = None) -> dict[str, Any]:
    """Check, without a model, that a provision is in force and that a quote from it is word for word.

    Also says whether the provision is binding. Guidance (G) and evidential provisions (E) are not.

    Args:
        reference: The provision's reference, for example "SYSC 15A.2.1R".
        quote: The quoted words, at least four.
        as_of: Use the rules in force on this date (YYYY-MM-DD). Leave out for today.
    """
    return verification.check_citation(reference, quote, _date(as_of))


HANDBOOK_TOOLS = [search_fca_handbook, get_fca_section, check_fca_citation]
