"""Find and normalise Handbook references such as "SYSC 15A.2.1R".

A reference has a key ("SYSC 15A.2.1") and sometimes a provision type ("R" rule, "G" guidance, "E" evidential...).
PR text can contain lookalikes ("TLS 1.3"), so callers must check that a reference exists before relying on it.
"""

import re
from dataclasses import dataclass

TYPES = ("UK", "EU", "R", "G", "E", "D", "P", "C")
_NUMBER = r"\d{1,2}[A-Z]{0,2}?(?:\.-?\d{1,3}[A-Z]{0,3}?){0,3}(?:\s+ANNEX\s+\d{1,2}[A-Z]{0,2}?(?:\.\d{1,3}[A-Z]{0,3}?){0,2})?"
_REFERENCE = re.compile(rf"\b([A-Z]{{2,9}})\s+({_NUMBER})\s*({'|'.join(TYPES)})?(?![\w])(?!\.\d)")


@dataclass(frozen=True)
class Reference:
    sourcebook: str
    number: str
    type: str | None = None

    @property
    def key(self) -> str:
        return f"{self.sourcebook} {self.number}"

    def __str__(self) -> str:
        return f"{self.key}{self.type or ''}"

    def candidate_keys(self) -> list[str]:
        """Keys this could mean. The E in "3.2.6E" may be a type, or part of the number."""
        return [self.key, f"{self.key}{self.type}"] if self.type else [self.key]


def find_references(text: str) -> list[Reference]:
    """Every distinct reference in the text, in order. Only upper-case sourcebook codes count in running text."""
    upper_annex = re.sub(r"\bAnnex\b", "ANNEX", text)
    return list(dict.fromkeys(Reference(s, " ".join(n.split()), t or None) for s, n, t in _REFERENCE.findall(upper_annex)))


def parse_reference(text: str) -> Reference | None:
    """The reference if the whole text is one, in any case, otherwise None."""
    match = _REFERENCE.fullmatch(" ".join(text.upper().split()))
    if not match:
        return None
    sourcebook, number, type_ = match.groups()
    return Reference(sourcebook, number, type_)


def section_of(key: str) -> str:
    """The section a key belongs to: "SYSC 15A.2.1" and "SYSC 15A.2" both give "SYSC 15A.2"."""
    sourcebook, _, number = key.partition(" ")
    if " ANNEX " in number:
        return f"{sourcebook} {re.sub(r'(ANNEX [^.]+).*', chr(92) + '1', number)}"
    return f"{sourcebook} {'.'.join(number.split('.')[:2])}"


def ancestors(key: str) -> list[str]:
    """The key's parents: "SYSC 15A.2.1" gives ["SYSC 15A.2", "SYSC 15A"]."""
    sourcebook, _, number = key.partition(" ")
    parts = number.split(".")
    return [f"{sourcebook} {'.'.join(parts[:i])}" for i in range(len(parts) - 1, 0, -1)]
