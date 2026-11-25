"""The format every Handbook snapshot is stored in, whatever the FCA API's own format turns out to be.

A snapshot is a folder holding `manifest.json`, `provisions.jsonl` and `glossary.jsonl`.
"""

import hashlib
import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from utils.references import TYPES, find_references, parse_reference, pretty, section_of


class Provision(BaseModel):
    """One version of one Handbook provision."""

    ref: str = Field(description="For example 'SYSC 15A.2.1R'")
    provision_type: str = Field(description="R rule, G guidance, E evidential, UK, EU, D, P, C; empty if none")
    heading_path: list[str] = Field(default_factory=list, description="Headings from sourcebook down to section")
    subheading: str | None = Field(default=None, description="The heading within the section it sits under, if any")
    text: str = Field(min_length=1)
    effective_from: date | None = Field(default=None, description="First day in force; None if not given")
    effective_to: date | None = Field(default=None, description="Last day in force, inclusive; None if open-ended")
    url: str | None = None

    @field_validator("ref")
    @classmethod
    def _valid_ref(cls, value: str) -> str:
        if parse_reference(value) is None:
            raise ValueError(f"not a Handbook reference: {value!r}")
        return value

    @field_validator("provision_type")
    @classmethod
    def _valid_type(cls, value: str) -> str:
        if value not in (*TYPES, ""):
            raise ValueError(f"unknown provision type {value!r}")
        return value

    @property
    def key(self) -> str:
        """The reference without its type. The stated type settles cases such as 3.2.6E + G."""
        compact = " ".join(self.ref.upper().split())
        return compact.removesuffix(self.provision_type).strip()

    @property
    def sourcebook(self) -> str:
        return self.key.split(" ")[0]

    @property
    def chapter(self) -> str:
        return self.key.split(" ANNEX ")[0].split(".")[0]

    @property
    def section(self) -> str:
        return section_of(self.key)

    @property
    def citation(self) -> str:
        return f"{pretty(self.key)}{self.provision_type}"

    @property
    def cites(self) -> list[str]:
        """The references in the text, as written, for example ["SYSC 4.1.1R"]."""
        return [str(ref) for ref in find_references(self.text) if self.key not in ref.candidate_keys()]


class GlossaryTerm(BaseModel):
    term: str
    definition: str


class Manifest(BaseModel):
    snapshot_date: date
    source: str = Field(description="'fca-handbook-api', or 'fictional-sample' for made-up test data")
    attribution: str
    sourcebooks: list[str] = Field(default_factory=list, description="What was downloaded; empty if not recorded")
    chapters: list[str] = Field(default_factory=list, description="Only these chapter IDs; empty for all of them")


class Snapshot(BaseModel):
    manifest: Manifest
    provisions: list[Provision]
    glossary: list[GlossaryTerm]
    sha256: str

    @classmethod
    def load(cls, folder: Path) -> "Snapshot":
        def rows(name: str) -> list[dict]:
            lines = (folder / name).read_text(encoding="utf-8").splitlines()
            return [json.loads(line) for line in lines if line.strip()]

        digest = hashlib.sha256()
        for name in ("provisions.jsonl", "glossary.jsonl"):
            digest.update((folder / name).read_bytes())
        snapshot = cls(
            manifest=Manifest.model_validate_json((folder / "manifest.json").read_text(encoding="utf-8")),
            provisions=[Provision(**row) for row in rows("provisions.jsonl")],
            glossary=[GlossaryTerm(**row) for row in rows("glossary.jsonl")],
            sha256=digest.hexdigest(),
        )
        seen = set()
        for p in snapshot.provisions:
            if (p.citation, p.effective_from) in seen:
                raise ValueError(f"{p.citation} from {p.effective_from} appears twice in the snapshot")
            seen.add((p.citation, p.effective_from))
        if not snapshot.provisions:
            raise ValueError("the snapshot has no provisions")
        return snapshot
