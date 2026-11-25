"""Plain-code checks on what an agent says about the Handbook. No model is involved."""

from datetime import date

from utils import store


def _normalise(text: str) -> str:
    straight = text.translate(str.maketrans("‘’“”–— ", "''\"\"-- "))
    return " ".join(straight.split())


def check_citation(ref: str, quote: str, as_of: date | None = None) -> list[str]:
    """Problems with a citation; an empty list means the provision is in force and the quote is word for word."""
    provision = store.get(ref, as_of or date.today())
    if provision is None:
        return [f"{ref} was not found in force in the index"]
    quote = _normalise(quote)
    if not quote:
        return [f"no quote was given for {ref}"]
    if quote not in _normalise(provision["text"]):
        return [f"the quote is not word for word in {provision['citation']}"]
    return []
