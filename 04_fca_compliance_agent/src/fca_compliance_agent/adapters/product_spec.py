"""Product spec module: a PDF, Markdown or text specification -> a compliance report.

Inbound  `raw_input` -> `analysis_input` (one AnalysisUnit per section) + `adapter_context` + `output_type`
    {"spec_path": "specs/bnpl.pdf"}                        a .pdf, .md or .txt file
    {"spec_base64": "JVBERi...", "spec_name": "bnpl.pdf"}  the same, already read
    {"spec_text": "# BNPL ...", "spec_name": "bnpl.md"}    the text itself

Outbound `findings_report` -> markdown + JSON in `reports/`, and a PDF unless `output_type` is "report".
"""

from __future__ import annotations

import base64
import binascii
import io
import re
from html import escape
from pathlib import Path
from typing import Any

from ..domain.models import AnalysisInput, AnalysisUnit, DeliverableResult, DocumentLocation, FindingsReport
from ..domain.state import PipelineState
from ..ports.adapters import ContextModule
from .output_type import InvalidInputError, decide_output_type

REPORTS_DIR = Path(__file__).resolve().parents[3] / "reports"  # 04_fca_compliance_agent/reports
SUFFIXES = (".pdf", ".md", ".txt")
MAX_BYTES = 20_000_000
MAX_UNITS = 100
MAX_UNIT_CHARS = 12_000
_HEADING = re.compile(r"^(?:#{1,6}\s+\S.*|(?:section\s+)?\d{1,2}(?:\.\d{1,2}){0,3}[.)]?\s+[A-Z][^.]{2,80})$", re.IGNORECASE)


def _pages(name: str, data: bytes) -> list[tuple[int | None, str]]:
    """(page number, text) for each page. Markdown and text have no pages."""
    if len(data) > MAX_BYTES:
        raise InvalidInputError(f"{name} is too large: the limit is {MAX_BYTES // 1_000_000} MB.")
    if not name.lower().endswith(".pdf"):
        return [(None, data.decode("utf-8", errors="replace"))]
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        return [(n, page.extract_text() or "") for n, page in enumerate(PdfReader(io.BytesIO(data)).pages, 1)]
    except PyPdfError as error:
        raise InvalidInputError(f"{name} could not be read as a PDF (it may be damaged or encrypted): {error}") from error


def _cut(text: str) -> list[str]:
    """Pieces no longer than a unit may be, split at line breaks where possible, so that nothing is lost."""
    pieces, current = [], ""
    for line in text.split("\n"):
        while len(line) > MAX_UNIT_CHARS:
            if current:
                pieces.append(current)
                current = ""
            pieces.append(line[:MAX_UNIT_CHARS])
            line = line[MAX_UNIT_CHARS:]
        if current and len(current) + len(line) + 1 > MAX_UNIT_CHARS:
            pieces.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    return [p for p in [*pieces, current] if p.strip()]


def split_spec(name: str, data: bytes) -> tuple[list[AnalysisUnit], int]:
    """One AnalysisUnit per section, and how many were left out because the document is too long."""
    sections: list[dict[str, Any]] = []
    for page, text in _pages(name, data):
        for line in text.splitlines():
            if _HEADING.match(line.strip()) or not sections:
                heading = line.strip().lstrip("# ") if _HEADING.match(line.strip()) else ""
                sections.append({"heading": heading, "start": page, "end": page, "lines": []})
            sections[-1]["lines"].append(line)
            sections[-1]["end"] = page
    units: list[AnalysisUnit] = []
    for section in sections:
        for piece in _cut("\n".join(section["lines"])):
            location = DocumentLocation(
                document_name=name, page_start=section["start"], page_end=section["end"],
                section_heading=section["heading"] or None,
            )  # fmt: skip
            units.append(
                AnalysisUnit(
                    unit_id=f"spec-{len(units) + 1}",
                    title=section["heading"] or name,
                    content=piece,
                    content_type="spec_section",
                    location=location,
                )
            )
    if not units:
        raise InvalidInputError(f"{name} has no readable text. A scanned document needs text recognition (OCR) first.")
    return units[:MAX_UNITS], max(0, len(units) - MAX_UNITS)


def _resolve_spec(raw: dict[str, Any]) -> tuple[str, bytes]:
    name = Path(str(raw.get("spec_name") or "specification.md")).name
    if text := raw.get("spec_text"):
        return (name if not name.lower().endswith(".pdf") else f"{name}.md"), str(text).encode()
    if encoded := raw.get("spec_base64"):
        if not name.lower().endswith(SUFFIXES):
            raise InvalidInputError(f"A specification must be a {', '.join(SUFFIXES)} file, not {name!r}.")
        try:
            return name, base64.b64decode(str(encoded), validate=True)
        except binascii.Error as error:
            raise InvalidInputError(f"{name} is not valid base64.") from error
    path = Path(str(raw.get("spec_path") or ""))
    if not raw.get("spec_path"):
        raise InvalidInputError("Give raw_input 'spec_path' (a .pdf, .md or .txt file), 'spec_base64' or 'spec_text'.")
    if path.suffix.lower() not in SUFFIXES:
        raise InvalidInputError(f"A specification must be a {', '.join(SUFFIXES)} file, not {path.name!r}.")
    if not path.is_file():
        raise InvalidInputError(f"There is no specification file called {path.name!r}.")
    if path.stat().st_size > MAX_BYTES:
        raise InvalidInputError(f"{path.name} is too large: the limit is {MAX_BYTES // 1_000_000} MB.")
    return path.name, path.read_bytes()


def product_spec_inbound(state: PipelineState) -> dict[str, Any]:
    raw = state.get("raw_input") or {}
    output_type = decide_output_type("product_spec", raw)
    name, data = _resolve_spec(raw)
    units, skipped = split_spec(name, data)
    return {
        "analysis_input": AnalysisInput(
            target_id=name,
            source_type="product_spec",
            title=str(raw.get("title") or f"Product specification: {name}"),
            # The document is author-controlled: the core prompt sees it as context, not instructions.
            summary=str(raw.get("summary") or units[0].content)[:2000],
            units=units,
        ),
        "adapter_context": {"document": name, "sections_skipped": skipped},
        "output_type": output_type,
    }


def _where(location: Any) -> str:
    if not isinstance(location, DocumentLocation):
        return ""
    start, end = location.page_start, location.page_end
    pages = "" if start is None else f"page {start}" if start == end else f"pages {start}-{end}"
    return ", ".join(part for part in (location.section_heading, pages) if part)


def render_spec_markdown(report: FindingsReport) -> str:
    lines = [
        f"## FCA / CCA product specification review: {report.title}",
        "",
        f"**Verdict:** `{report.overall_verdict}` · **Findings:** {len(report.findings)} · "
        f"**Sections reviewed:** {report.units_triaged_in}/{report.units_total}",
        "",
        report.executive_summary,
        "",
    ]
    for f in report.findings:
        refs = [c.reference for c in f.fca_citations + f.cca_citations]
        lines += [
            f"### [{f.severity.upper()}] {f.title}",
            f"{_where(f.location)} · status `{f.status}` · confidence {f.confidence:.0%}",
            "",
            f.description,
            "",
            f"- **Rules:** {', '.join(refs) or 'none cited'}",
            f"- **Fix:** {f.remediation_guidance}",
            "",
        ]
    lines.append("_Automated AI review. Not legal, compliance or regulatory advice; a human must confirm._")
    return "\n".join(lines)


def write_pdf(markdown: str, path: Path) -> None:
    """A plain PDF of the report. Its built-in font has no characters outside Western European text."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    story: list[Any] = []
    for line in markdown.splitlines():
        text = re.sub(r"\*\*|`|^_|_$", "", line.lstrip("# ") if line.startswith("#") else line).strip()
        if not text:
            story.append(Spacer(1, 6))
            continue
        style = "Heading2" if line.startswith("## ") else "Heading3" if line.startswith("#") else "BodyText"
        # Escaped, because report text comes from a model and a document, and Paragraph reads markup.
        story.append(Paragraph(escape(text), styles[style]))
    SimpleDocTemplate(str(path), pagesize=A4, title="FCA / CCA product specification review").build(story)


def make_product_spec_outbound(*, reports_dir: Path | None = None):
    def product_spec_outbound(state: PipelineState) -> dict[str, Any]:
        report, output_type = state["findings_report"], state.get("output_type", "report")
        out_dir = reports_dir or REPORTS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        markdown = render_spec_markdown(report)
        md_path, json_path = out_dir / f"{report.report_id}.md", out_dir / f"{report.report_id}.json"
        md_path.write_text(markdown, encoding="utf-8")
        json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        artifacts = [str(md_path), str(json_path)]
        if output_type == "pdf_report":
            pdf_path = out_dir / f"{report.report_id}.pdf"
            write_pdf(markdown, pdf_path)
            artifacts.append(str(pdf_path))
        return {
            "deliverable_result": DeliverableResult(
                module_name="product_spec_review",
                status="completed",
                output_type=output_type,
                summary=f"{report.overall_verdict}: {len(report.findings)} finding(s) for {report.target_id}. "
                f"Saved to {artifacts[-1]}.",
                artifacts=artifacts,
                metadata={"severity_counts": report.severity_counts()},
            )
        }

    return product_spec_outbound


def build_product_spec_review_module(*, reports_dir: Path | None = None) -> ContextModule:
    return ContextModule(
        name="product_spec_review",
        description="Product specification (PDF, Markdown or text) -> markdown/JSON report + PDF",
        inbound_node=product_spec_inbound,
        outbound_node=make_product_spec_outbound(reports_dir=reports_dir),
    )
