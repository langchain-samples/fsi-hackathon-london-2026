"""Turn the FCA Handbook API's responses into a snapshot that scripts.ingest can read.

The API gives a provision's name without its type letter ("SYSC 4.1.1", "Rules") and its text as HTML.
"""

import html
import json
import re
from collections import Counter
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

from utils.references import parse_reference
from utils.schema import GlossaryTerm, Manifest, Provision, Snapshot

SITE = "https://handbook.fca.org.uk"
ATTRIBUTION = "© FCA. Contains FCA Handbook material used under the Open Government Licence v3.0."
_TYPES = {
    "Rules": "R", "Guidance": "G", "Evidential Provisions": "E", "Directions": "D", "Statements of Principle": "P",
    "UK": "UK", "UK Legislation": "UK", "EU": "EU", "EU Legislation": "EU", "Conduct": "C",
}  # fmt: skip
_BLOCKS = {"p", "div", "li", "tr", "br", "table", "ol", "ul", "h1", "h2", "h3", "h4", "h5", "h6", "figure", "caption"}
_CELLS = {"td", "th"}
_MAX_SKIPPED = 0.02  # more than this means the format has changed


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self._hidden += 1
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._hidden = max(0, self._hidden - 1)
        elif tag in _CELLS:
            self.parts.append(" | ")
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._hidden:
            self.parts.append(data.replace("\n", " ").replace("\t", " "))


_LABEL = re.compile(r"^\(?[0-9A-Za-z]{1,5}\)$|^[0-9]{1,3}\.$")


def to_text(markup: str) -> str:
    """Plain text: one paragraph or table row to a line, and a label such as "(1)" on the line it labels."""
    parser = _Text()
    parser.feed(markup)
    parser.close()
    lines = (re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).split("\n"))
    joined: list[str] = []
    for line in lines:
        line = re.sub(r"(\s*\|\s*)+$", "", line).strip()
        line = re.sub(r"^(\|\s*)+", "", line)
        if not line:
            continue
        if joined and _LABEL.match(joined[-1]):
            joined[-1] = f"{joined[-1]} {line}"
        else:
            joined.append(line)
    return "\n".join(joined)


def chapters_of(tree: dict, sourcebooks: list[str]) -> list[dict]:
    """The chapters of some sourcebooks, in Handbook order."""
    found: list[dict] = []

    def walk(node: dict, book: str | None) -> None:
        if node["type"] == "sourcebook":
            book = node["entityId"].upper()
        if node["type"] == "chapter" and book in sourcebooks and not node.get("isDeleted"):
            sections = {c["name"]: c["entityId"] for c in node.get("parts") or [] if c["type"] == "section"}
            found.append({"id": node["entityId"], "name": node["name"], "sourcebook": book, "sections": sections})
        for child in node.get("parts") or []:
            walk(child, book)

    for node in tree["tree"]:
        walk(node, None)
    return found


def select_chapters(tree: dict, sourcebooks: list[str], only: list[str] | None = None) -> list[dict]:
    """The chapters asked for. A name that is not in the Handbook, such as a typo, is refused and not skipped."""
    found = chapters_of(tree, sourcebooks)
    books, ids = {c["sourcebook"] for c in found}, {c["id"] for c in found}
    if unknown := [name for name in sourcebooks if name not in books] + [i for i in only or [] if i not in ids]:
        raise ValueError(f"not in the Handbook, or not in those sourcebooks: {', '.join(unknown)}")
    return [c for c in found if not only or c["id"] in only]


def _check_same_snapshot(folder: Path, manifest: Manifest) -> None:
    """Never replace a snapshot with one of another day, or one that covers something else."""
    path = folder / "manifest.json"
    if not path.exists():
        return
    old = Manifest.model_validate_json(path.read_text(encoding="utf-8"))
    if old.snapshot_date != manifest.snapshot_date:
        raise ValueError(f"{folder} holds the snapshot of {old.snapshot_date}. Use another --out.")
    if old.sourcebooks and (old.sourcebooks, old.chapters) != (manifest.sourcebooks, manifest.chapters):
        raise ValueError(f"{folder} holds a snapshot of other sourcebooks or chapters. Use another --out.")


def _is_deleted(text: str) -> bool:
    return re.fullmatch(r"\W*(deleted|not used|omitted)\W*", text, flags=re.IGNORECASE) is not None


def provision_of(record: dict, chapter: dict) -> Provision | str | None:
    """The provision, None if it has been deleted, or why it could not be used."""
    name = " ".join((record["ProvisionName"] or "").split())
    text = to_text(record["Content"] or "")
    if not text or _is_deleted(text):
        return None
    kind = _TYPES.get(record["ProvisionType"] or "")
    if not record["ProvisionType"]:
        kind = ""  # transitional tables and schedules, which the API gives no type
    if kind is None:
        return f"{name}: unknown type {record['ProvisionType']!r}"
    # A few names already include the type: "COBS 19.5.19 G", "SYSC 19G Annex 1R".
    typed = bool(kind) and re.search(rf"(?: {kind}| annex \S*\d{kind})$", name, flags=re.IGNORECASE)
    ref = name if typed else f"{name}{kind}"
    if parse_reference(ref) is None:
        return f"{name}: not a reference this code understands"
    section_id = chapter["sections"].get(record["SectionName"])
    page = f"{SITE}/handbook/{chapter['id']}" + (f"/{section_id}" if section_id else "")
    return Provision(
        ref=ref, provision_type=kind, text=text, subheading=record["SubSectionName"] or None,
        heading_path=[h for h in (record["SourcebookName"], record["ChapterName"], record["SectionName"]) if h],
        effective_from=date.fromisoformat(record["ProvisionDate"][:10]) if record["ProvisionDate"] else None,
        url=f"{page}#{record['ProvisionId']}" if record["ProvisionId"] else page,
    )  # fmt: skip


def convert(folder: Path, day: str, sourcebooks: list[str], only: list[str] | None = None) -> dict:
    """Write the snapshot from the raw responses, if they are complete and make sense. Returns a report."""
    raw = folder / "raw"
    tree = json.loads((raw / "tree.json").read_text(encoding="utf-8"))
    wanted = select_chapters(tree, sourcebooks, only)
    manifest = Manifest(
        snapshot_date=date.fromisoformat(day), source="fca-handbook-api", attribution=ATTRIBUTION,
        sourcebooks=sorted(sourcebooks), chapters=sorted(only or []),
    )  # fmt: skip
    _check_same_snapshot(folder, manifest)
    provisions: list[Provision] = []
    skipped: list[str] = []
    deleted = records = joined = 0
    seen: dict[tuple, Provision] = {}
    empty: list[str] = []
    for chapter in wanted:
        found = json.loads((raw / "chapters" / f"{chapter['id']}.json").read_text(encoding="utf-8"))["provision"]
        if not found:
            empty.append(chapter["id"])
        for record in found:
            records += 1
            result = provision_of(record, chapter)
            if result is None:
                deleted += 1
            elif isinstance(result, str):
                skipped.append(result)
            elif (result.citation, result.effective_from) in seen:  # the API splits a few provisions in two
                first = seen[(result.citation, result.effective_from)]
                first.text += "\n" + result.text
                joined += 1
            else:
                provisions.append(result)
                seen[(result.citation, result.effective_from)] = result

    counts = Counter(p.sourcebook for p in provisions)
    if missing := [b for b in sourcebooks if not counts[b] and any(c["sourcebook"] == b for c in wanted)]:
        raise ValueError(f"no provisions were found for {', '.join(missing)}")
    if len(skipped) > _MAX_SKIPPED * records:
        raise ValueError(f"{len(skipped)} of {records} provisions could not be read, for example {skipped[:5]}")

    glossary: dict[str, GlossaryTerm] = {}
    for entry in json.loads((raw / "glossary.json").read_text(encoding="utf-8"))["glossary"]:
        term, definition = html.unescape(entry["Term"] or "").strip(), to_text(entry["GlossaryHtml"] or "")
        if term and definition and not _is_deleted(definition):
            if term in glossary:
                definition = f"{glossary[term].definition}\n{definition}"
            glossary[term] = GlossaryTerm(term=term, definition=definition)

    _write(folder / "provisions.jsonl", provisions)
    _write(folder / "glossary.jsonl", list(glossary.values()))
    (folder / "manifest.json").write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    Snapshot.load(folder)  # the same checks that ingest makes
    return {
        "snapshot": str(folder), "chapters": len(wanted), "provisions": len(provisions), "by_sourcebook": dict(counts),
        "by_type": dict(Counter(p.provision_type for p in provisions)), "glossary_terms": len(glossary),
        "deleted_in_handbook": deleted, "joined": joined, "could_not_read": skipped, "chapters_with_nothing_in_force": empty,
    }  # fmt: skip


def _write(path: Path, rows: list) -> None:
    path.write_text("".join(row.model_dump_json() + "\n" for row in rows), encoding="utf-8")
