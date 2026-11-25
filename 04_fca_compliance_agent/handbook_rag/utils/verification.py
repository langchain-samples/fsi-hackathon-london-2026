"""Plain-code checks on what an agent says about the Handbook. No model is involved."""

from datetime import date

from utils import store

_MIN_WORDS = 4  # a shorter "quote", such as "must", would match almost anything


def _normalise(text: str) -> str:
    straight = text.translate(str.maketrans("‘’“”–— ", "''\"\"-- "))
    return " ".join(straight.split())


def check_citation(reference: str, quote: str, as_of: date | None = None) -> dict:
    """Whether a provision is in force and the quote is word for word, and whether it is a binding rule."""
    row = store.get(reference, as_of or date.today())
    if row is None:
        return {"ok": False, "problems": [f"{reference} was not found in force in the index"]}
    quote, problems = _normalise(quote), []
    if len(quote.split()) < _MIN_WORDS and quote != _normalise(row["text"]):
        problems.append(f"the quote from {row['citation']} is too short to check; give at least {_MIN_WORDS} words")
    elif quote not in _normalise(row["text"]):
        problems.append(f"the quote is not word for word in {row['citation']}")
    return {
        "ok": not problems, "problems": problems, "reference": row["citation"],
        "provision_type": row["provision_type"], "binding": store.is_binding(row["provision_type"]),
        "source": row["source"],
    }  # fmt: skip
