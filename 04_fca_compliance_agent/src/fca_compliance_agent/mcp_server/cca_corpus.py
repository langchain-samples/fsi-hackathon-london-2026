"""Consumer Credit Act 1974 section index, built from the legislation.gov.uk text in `cca.md`.

The parser handles the layout of that copy: footnote blocks, page headers, omitted sections, and footnote markers
attached to headings (for example `F4055A Pre-contractual explanations`). Each section becomes one passage with the
reference `CCA 1974 s.<number>`, and a BM25 index ranks sections for search.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .bm25 import BM25

OMITTED_TEXT = "[No operative text in the source: omitted, repealed or prospective.]"

DEFAULT_CCA_PATH = Path(__file__).resolve().parents[4] / "cca.md"

_PAGE_NOISE = re.compile(
    r"^(Document Generated:|Status:|Changes to legislation:|\d{1,2} [A-Z][a-z]+ \d{4}\. There are changes|"
    r"appear in the content|View outstanding changes|PROSPECTIVE$|Part [IVXA]+ [–-]|PART [IVXA]+ [–-]|"
    r"\s*\.(\s*\.){5,}\s*$|PART [IVXA]+$|[A-Z][A-Z ,\-–]{8,}$)"
)
_PAGE_HEADER = re.compile(r"^(\d{1,3} )?Consumer Credit Act 1974 \(c\. 39\)( \d{1,3})?\s*$")
_FOOTNOTE_MARKER = re.compile(
    r"^(Textual Amendments|Modifications etc\. \(not altering text\)|Commencement Information|"
    r"Editorial Information|Marginal Citations)\s*$"
)
_PLAIN_HEADING = re.compile(r"^(\d{1,3}[A-Z]{0,2}) ([A-Z][^\n]{1,160})$")
# Footnote markers attached to the number. The title is optional because some copies print the number alone.
_MARKED_HEADING = re.compile(r"^((?:\[?F\d+\]?)+)(\d+)([A-Z]{0,2})\.?(?:\s+([A-Z][^\n]{1,160}))?$")


@dataclass(frozen=True)
class CCASection:
    number: str  # "55", "55A", "66A"
    title: str
    text: str

    @property
    def reference(self) -> str:
        return f"CCA 1974 s.{self.number}"


def normalise_section(value: str) -> str:
    """Accept 's.55', 'section 55A', '55a' and return '55' or '55A'."""
    cleaned = re.sub(r"^(section|sec\.?|s\.?)\s*", "", value.strip(), flags=re.IGNORECASE)
    return cleaned.upper().replace(" ", "")


def _clean_title(title: str) -> str:
    title = re.sub(r"\[F\d+[^\]]*\]", "", title)
    title = re.sub(r"\bF\d+\b\s*", "", title)
    return re.sub(r"\s+", " ", title).strip().rstrip(".").strip()


def _split_marked(match: re.Match, last_base: int) -> tuple[str, str] | None:
    """Resolve `F4055A` into section 55A. The footnote and section numbers run together, so pick the smallest split
    whose section number is not before the previous section."""
    footnote_part, digits, letters, title = match.groups()
    all_digits = "".join(re.findall(r"\d+", footnote_part)) + digits
    best: int | None = None
    for cut in range(1, len(all_digits)):
        value = int(all_digits[cut:])
        if last_base <= value <= 300 and (best is None or value < best):
            best = value
    if best is None:
        return None
    return f"{best}{letters}", title or ""


def parse_cca_text(lines: list[str]) -> list[CCASection]:
    sections: dict[str, tuple[str, str]] = {}
    current: str | None = None
    current_title = ""
    body: list[str] = []
    last_base = 0
    in_footnotes = False

    def close() -> None:
        if current is not None and current not in sections:
            text = "\n".join(body).strip()
            sections[current] = (current_title, text or OMITTED_TEXT)

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("SCHEDULE"):
            break  # the operative sections end before the Schedules
        if _PAGE_HEADER.match(line):
            in_footnotes = False
            continue
        if _FOOTNOTE_MARKER.match(line):
            in_footnotes = True
            continue
        if _PAGE_NOISE.match(line):
            continue

        # Headings are checked even in footnote mode, because a heading can follow a footnote block without a page
        # break. Footnote items start with a marker and a space ('F38 Words ...') and so never match.
        marked = _MARKED_HEADING.match(line)
        if marked:
            split = _split_marked(marked, last_base)
            if split is not None:
                number, title = split
                close()
                current, current_title, body = number, _clean_title(title), []
                last_base = int(re.match(r"\d+", number).group())
                in_footnotes = False
                continue

        plain = _PLAIN_HEADING.match(line)
        if plain and "(c. 39)" not in line:
            base = int(re.match(r"\d+", plain.group(1)).group())
            if base >= last_base:  # section numbers never go backwards; this rejects stray numbered lines
                close()
                current, current_title, body = plain.group(1), _clean_title(plain.group(2)), []
                last_base = base
                in_footnotes = False
                continue

        if in_footnotes or current is None:
            continue
        body.append(line if line.strip() else "")

    close()
    return [CCASection(number=n, title=t or "(title not printed in source)", text=x) for n, (t, x) in sections.items()]


def load_cca(path: Path = DEFAULT_CCA_PATH) -> list[CCASection]:
    if not path.exists():
        raise FileNotFoundError(f"Consumer Credit Act text not found at {path}")
    sections = parse_cca_text(path.read_text(encoding="utf-8").splitlines())
    if not sections:
        raise ValueError(f"No sections parsed from {path}")
    return sections


PASSAGE_CHARS = 1200


@dataclass(frozen=True)
class CCAPassage:
    """A passage-sized part of one section. Search returns these, so results stay focused on the matching text."""

    number: str
    title: str
    part: int
    parts: int
    text: str

    @property
    def reference(self) -> str:
        return f"CCA 1974 s.{self.number}"


def _split_passages(text: str, limit: int = PASSAGE_CHARS) -> list[str]:
    """Group a section's subsections into passages of about `limit` characters. A subsection longer than the limit is
    split at sentence ends."""
    paragraphs: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.startswith("(") or not paragraphs:
            paragraphs.append(line)
        else:
            paragraphs[-1] = f"{paragraphs[-1]} {line}"

    pieces: list[str] = []
    for para in paragraphs:
        while len(para) > limit:
            cut = para.rfind(". ", 0, limit)
            cut = cut + 1 if cut > limit // 2 else limit
            pieces.append(para[:cut].strip())
            para = para[cut:].strip()
        pieces.append(para)

    passages: list[str] = []
    buffer = ""
    for piece in pieces:
        if buffer and len(buffer) + len(piece) + 1 > limit:
            passages.append(buffer)
            buffer = piece
        else:
            buffer = f"{buffer}\n{piece}".strip() if buffer else piece
    if buffer:
        passages.append(buffer)
    return passages or [text]


class CCAIndex:
    """The Act's sections, split into passages, with BM25 search. The title is weighted above the body text."""

    def __init__(self, sections: list[CCASection]) -> None:
        self.sections = sections
        self._by_number = {s.number: s for s in sections}
        self.passages: list[CCAPassage] = []
        for section in sections:
            pieces = _split_passages(section.text) if section.text != OMITTED_TEXT else [OMITTED_TEXT]
            for i, piece in enumerate(pieces, start=1):
                self.passages.append(
                    CCAPassage(section.number, section.title, i, len(pieces), piece)
                )
        self._bm25 = BM25(
            self.passages,
            fields=lambda p: [(p.title, 3), (p.text, 1)],
        )

    def get(self, number: str) -> CCASection | None:
        return self._by_number.get(normalise_section(number))

    def search(
        self,
        query: str,
        limit: int = 5,
        numbers: set[str] | None = None,
    ) -> list[tuple[CCAPassage, float]]:
        """Best passage per section, so one long section cannot fill every result."""
        candidates = self._bm25.search(
            query,
            limit=limit * 20,
            keep=(lambda p: p.number in numbers) if numbers is not None else None,
        )
        best: dict[str, tuple[CCAPassage, float]] = {}
        for passage, score in candidates:
            if passage.number not in best:
                best[passage.number] = (passage, score)
        return list(best.values())[:limit]
