"""Look up a regulatory document in the firm's document library (`data/documents.jsonl`).

Each row describes one document, and its `file` column names the Markdown file in `data/documents` that holds the full text.
"""
import re

from pydantic import BaseModel, Field
from utils import db
from utils.db import DATA_DIR


class DocumentSection(BaseModel):
    """One section of a document, split at its '## ' heading."""

    number: str = Field(description="The section number, for example '4'")
    title: str = Field(description="The section title")
    text: str = Field(description="The full text of the section, without its heading")


class Document(BaseModel):
    """A regulatory document, split into its sections."""

    document_id: str = Field(description="The document's ID in the library, for example 'PS26-4'")
    title: str = Field(description="The document title")
    sections: list[DocumentSection] = Field(description="The document's sections, in order")


def _split_into_sections(text: str) -> list[DocumentSection]:
    """Split a document's text at its '## ' headings. Each section keeps its number, title and full text."""
    sections = []
    for block in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        heading, _, body = block.partition("\n")
        number, _, title = heading.partition(". ")
        sections.append(DocumentSection(number=number.strip(), title=title.strip(), text=body.strip()))
    return sections

def get_document(document_id: str) -> dict:
    """A document in the library, for example "PS26-4", as a JSON object with its ID, title and sections.

    Each section has its number, title and full text.
    """
    row = db.get("documents", document_id=document_id)
    if row is None:
        raise ValueError(f"There is no document {document_id} in the library.")

    text = (DATA_DIR / "documents" / row["document_file"]).read_text(encoding="utf-8")
    
    document = Document(
        document_id=document_id,
        title=text.splitlines()[0].lstrip("# ").strip(),
        sections=_split_into_sections(text),
    )

    return document.model_dump()
