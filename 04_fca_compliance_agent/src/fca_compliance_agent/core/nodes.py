"""Core LangGraph nodes for FCA Handbook & Consumer Credit Act compliance analysis.

All nodes are created via factory functions accepting Port protocols (dependency injection)
so the Core remains 100% decoupled from MCP transports, LLM providers, and I/O adapters.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Literal

from langgraph.graph import END
from langgraph.types import Send, interrupt

from ..domain.models import (
    SEVERITY_RANK,
    ComplianceVerdict,
    DeliverableResult,
    Finding,
    FindingsReport,
    HumanReviewDecision,
    RegulatoryPassage,
    TriagedUnit,
)
from ..domain.state import CoreAnalysisState, PipelineState, UnitAnalysisState
from ..ports.llm import ComplianceAnalyzerPort
from ..ports.regulatory import ConsumerCreditActPort, FCAHandbookPort


def _dedupe_passages(passages: list[RegulatoryPassage]) -> list[RegulatoryPassage]:
    seen: set[tuple[str, str]] = set()
    unique: list[RegulatoryPassage] = []
    for passage in passages:
        key = (passage.source, passage.reference.strip().upper())
        if key not in seen:
            seen.add(key)
            unique.append(passage)
    return unique


def _dedupe_and_rank_findings(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    unique: list[Finding] = []
    for finding in findings:
        refs = tuple(
            sorted(
                [c.reference.strip().upper() for c in finding.fca_citations]
                + [c.reference.strip().upper() for c in finding.cca_citations]
            )
        )
        dedupe_key = (finding.unit_id, finding.title.strip().lower(), refs)
        if dedupe_key not in seen:
            seen.add(dedupe_key)
            unique.append(finding)

    unique.sort(key=lambda f: (SEVERITY_RANK[f.severity], f.unit_id, f.title.lower()))
    return [
        finding.model_copy(update={"finding_id": f"F-{idx:03d}"})
        for idx, finding in enumerate(unique, start=1)
    ]


def _compute_verdict(findings: list[Finding]) -> ComplianceVerdict:
    if not findings:
        return "compliant"
    if any(
        f.severity in ("critical", "high") and f.status in ("non_compliant", "potential_breach")
        for f in findings
    ):
        return "non_compliant"
    if any(f.severity == "medium" or f.status == "needs_review" for f in findings):
        return "requires_manual_review"
    return "compliant_with_observations"


def make_triage_node(analyzer_port: ComplianceAnalyzerPort):
    """Create the `triage_units` node that filters units and drafts regulatory queries."""

    async def triage_units(state: CoreAnalysisState) -> dict[str, Any]:
        analysis_input = state["analysis_input"]
        if not analysis_input.units:
            return {"triaged_units": []}

        triaged = await analyzer_port.triage_units(analysis_input)
        # Ensure every unit has a triage entry (default relevant if omitted by model)
        triaged_by_id = {t.unit_id: t for t in triaged}
        normalised: list[TriagedUnit] = []
        for unit in analysis_input.units:
            if unit.unit_id in triaged_by_id:
                normalised.append(triaged_by_id[unit.unit_id])
            else:
                normalised.append(
                    TriagedUnit(
                        unit_id=unit.unit_id,
                        is_relevant=True,
                        rationale="Included by default (no explicit triage decision).",
                        fca_queries=[unit.title],
                        cca_queries=[unit.title],
                    )
                )
        return {"triaged_units": normalised}

    return triage_units


def route_triaged_units(
    state: CoreAnalysisState,
) -> list[Send] | Literal["synthesize_report"]:
    """Fan out relevant units in parallel via LangGraph `Send` API, or jump to synthesis."""
    analysis_input = state["analysis_input"]
    units_by_id = {u.unit_id: u for u in analysis_input.units}
    triaged = state.get("triaged_units", [])

    sends: list[Send] = []
    for decision in triaged:
        if decision.is_relevant and decision.unit_id in units_by_id:
            payload: UnitAnalysisState = {
                "analysis_input_summary": analysis_input.summary or analysis_input.title,
                "regulatory_scope": analysis_input.regulatory_scope,
                "unit": units_by_id[decision.unit_id],
                "triage": decision,
            }
            sends.append(Send("analyze_unit", payload))

    if not sends:
        return "synthesize_report"
    return sends


def make_analyze_unit_node(
    *,
    fca_port: FCAHandbookPort,
    cca_port: ConsumerCreditActPort,
    analyzer_port: ComplianceAnalyzerPort,
    max_passages_per_query: int = 5,
):
    """Create the `analyze_unit` worker node invoked in parallel per triaged unit."""

    async def analyze_unit(state: UnitAnalysisState) -> dict[str, Any]:
        unit = state["unit"]
        triage = state["triage"]
        scope = state.get("regulatory_scope") or None

        fca_queries = triage.fca_queries or [unit.title]
        cca_queries = triage.cca_queries or [unit.title]

        executed_queries: list[str] = []
        fca_tasks = []
        for q in fca_queries:
            executed_queries.append(f"FCA:{q}")
            fca_tasks.append(
                fca_port.search_rules(q, sourcebooks=scope, top_k=max_passages_per_query)
            )

        cca_tasks = []
        for q in cca_queries:
            executed_queries.append(f"CCA:{q}")
            cca_tasks.append(
                cca_port.query_provisions(
                    q,
                    sections=triage.cca_sections or None,
                    top_k=max_passages_per_query,
                )
            )
        for sec in triage.cca_sections:
            executed_queries.append(f"CCA_DOC:{sec}")
            cca_tasks.append(cca_port.get_document(section=sec))

        fca_results, cca_results = await asyncio.gather(
            asyncio.gather(*fca_tasks) if fca_tasks else asyncio.sleep(0, result=[]),
            asyncio.gather(*cca_tasks) if cca_tasks else asyncio.sleep(0, result=[]),
        )

        fca_passages = _dedupe_passages(
            [p for batch in fca_results for p in batch]
        )
        cca_passages = _dedupe_passages(
            [p for batch in cca_results for p in batch]
        )

        # Fallback to general CCA document fetch if query returned nothing
        if not cca_passages:
            executed_queries.append("CCA_DOC:ALL")
            cca_passages = _dedupe_passages(await cca_port.get_document(section=None))

        findings = await analyzer_port.analyze_unit(
            unit=unit,
            analysis_input_summary=state["analysis_input_summary"],
            fca_passages=fca_passages,
            cca_passages=cca_passages,
        )

        return {
            "unit_findings": findings,
            "queries_executed": executed_queries,
        }

    return analyze_unit


def make_synthesize_report_node(analyzer_port: ComplianceAnalyzerPort):
    """Create the `synthesize_report` reducer node that builds the final FindingsReport."""

    async def synthesize_report(state: CoreAnalysisState) -> dict[str, Any]:
        analysis_input = state["analysis_input"]
        triaged = state.get("triaged_units", [])
        raw_findings = state.get("unit_findings", [])
        queries = list(dict.fromkeys(state.get("queries_executed", [])))

        findings = _dedupe_and_rank_findings(raw_findings)
        verdict = _compute_verdict(findings)
        executive_summary = await analyzer_port.synthesize_executive_summary(
            analysis_input=analysis_input,
            findings=findings,
        )
        # Written by code, so the model's summary cannot leave it out.
        notes = list(dict.fromkeys(state.get("review_notes", [])))
        executive_summary = "\n\n".join([*notes, executive_summary])

        units_triaged_in = sum(1 for t in triaged if t.is_relevant)
        units_total = len(analysis_input.units)
        units_skipped = max(0, units_total - units_triaged_in)

        report = FindingsReport(
            target_id=analysis_input.target_id,
            source_type=analysis_input.source_type,
            title=analysis_input.title,
            overall_verdict=verdict,
            executive_summary=executive_summary,
            findings=findings,
            units_total=units_total,
            units_triaged_in=units_triaged_in,
            units_skipped=units_skipped,
            regulatory_queries_executed=queries,
        )
        return {"findings_report": report}

    return synthesize_report


# ---------------------------------------------------------------------------
# Optional Human-in-the-Loop Governance Gate (Between Core & Outbound Adapter)
# ---------------------------------------------------------------------------


def _parse_review_resume(raw: Any) -> HumanReviewDecision:
    if isinstance(raw, HumanReviewDecision):
        return raw
    if isinstance(raw, dict):
        return HumanReviewDecision.model_validate(raw)
    if isinstance(raw, str):
        cleaned = raw.strip()
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return HumanReviewDecision.model_validate(parsed)
        except json.JSONDecodeError:
            pass
        lowered = cleaned.lower().strip("\"'")
        if lowered in ("approve", "approved", "yes", "ok"):
            return HumanReviewDecision(action="approve", reviewer_notes=cleaned)
        if lowered in ("reject", "rejected", "no", "abort"):
            return HumanReviewDecision(action="reject", reviewer_notes=cleaned)
    return HumanReviewDecision(action="reject", reviewer_notes=f"Unrecognised resume payload: {raw!r}")


def make_human_review_gate_node(default_require_human_approval: bool = False):
    """Create the optional HITL `interrupt()` checkpoint node between Core and Outbound."""

    def human_review_gate(state: PipelineState) -> dict[str, Any]:
        require_approval = state.get("require_human_approval", default_require_human_approval)
        report = state["findings_report"]

        if not require_approval:
            return {
                "human_review": HumanReviewDecision(
                    action="approve",
                    reviewer_notes="Auto-approved (HITL approval gate disabled).",
                )
            }

        resume_value = interrupt(
            {
                "report_id": report.report_id,
                "target_id": report.target_id,
                "source_type": report.source_type,
                "overall_verdict": report.overall_verdict,
                "executive_summary": report.executive_summary,
                "findings": [
                    {
                        "finding_id": f.finding_id,
                        "severity": f.severity,
                        "status": f.status,
                        "title": f.title,
                        "fca_refs": [c.reference for c in f.fca_citations],
                        "cca_refs": [c.reference for c in f.cca_citations],
                    }
                    for f in report.findings
                ],
                "instructions": (
                    "Reply with {'action': 'approve' | 'filter' | 'reject', "
                    "'approved_finding_ids': [...], 'reviewer_notes': '...'}"
                ),
            }
        )

        decision = _parse_review_resume(resume_value)
        updates: dict[str, Any] = {"human_review": decision}

        if decision.action == "filter" and decision.approved_finding_ids is not None:
            allowed = set(decision.approved_finding_ids)
            filtered_findings = [f for f in report.findings if f.finding_id in allowed]
            updates["findings_report"] = report.model_copy(
                update={
                    "findings": filtered_findings,
                    "overall_verdict": _compute_verdict(filtered_findings),
                }
            )
        elif decision.action == "reject":
            updates["deliverable_result"] = DeliverableResult(
                module_name="human_review_gate",
                status="aborted_by_reviewer",
                summary=f"Outbound delivery aborted by human reviewer: {decision.reviewer_notes or 'no notes'}.",
            )

        return updates

    return human_review_gate


def route_after_human_review(state: PipelineState) -> Literal["outbound_adapter", "__end__"]:
    """Route to Outbound Adapter if approved/filtered, or END if rejected."""
    decision = state.get("human_review")
    if decision is not None and decision.action == "reject":
        return END
    return "outbound_adapter"
