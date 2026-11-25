"""LangChain-backed ComplianceAnalyzerPort using the repo's LangSmith LLM Gateway."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from ...core.prompts import (
    build_executive_summary_prompt,
    build_triage_prompt,
    build_unit_analysis_prompt,
)
from ...domain.models import (
    AnalysisInput,
    AnalysisUnit,
    Finding,
    FindingStatus,
    RegulatoryCitation,
    RegulatoryPassage,
    Severity,
    TriagedUnit,
)


class _TriageBatchOutput(BaseModel):
    decisions: list[TriagedUnit] = Field(default_factory=list)


class _DraftFinding(BaseModel):
    title: str
    description: str
    severity: Severity
    status: FindingStatus = "potential_breach"
    evidence_excerpt: str = ""
    fca_citations: list[RegulatoryCitation] = Field(default_factory=list)
    cca_citations: list[RegulatoryCitation] = Field(default_factory=list)
    fca_vs_cca_comparison: str = ""
    remediation_guidance: str
    suggested_change: str | None = None
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)


class _UnitFindingsOutput(BaseModel):
    findings: list[_DraftFinding] = Field(default_factory=list)


def load_repo_env() -> None:
    """Load the repository root `.env` (matching workshops 01–03)."""
    # File lives at: <repo>/04_fca_compliance_agent/src/fca_compliance_agent/adapters/llm/langchain_llm.py
    repo_root = Path(__file__).resolve().parents[5]
    env_path = repo_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()


def resolve_default_model_name() -> str:
    """Load `MODEL_NAME` from the repo root `.env` (matching workshops 01–03)."""
    load_repo_env()
    return os.environ.get("MODEL_NAME", "openai:gpt-6-luna")


class LangChainComplianceAnalyzer:
    """Production LLM adapter implementing `ComplianceAnalyzerPort` via LangChain."""

    def __init__(self, model_name: str | None = None) -> None:
        load_repo_env()
        self._model_name = model_name or resolve_default_model_name()

    def _model(self):
        return init_chat_model(self._model_name)

    async def triage_units(self, analysis_input: AnalysisInput) -> list[TriagedUnit]:
        prompt = build_triage_prompt(analysis_input)
        structured = self._model().with_structured_output(
            _TriageBatchOutput, method="json_schema"
        )
        result: _TriageBatchOutput = await structured.ainvoke(prompt)
        return result.decisions

    async def analyze_unit(
        self,
        *,
        unit: AnalysisUnit,
        analysis_input_summary: str,
        fca_passages: list[RegulatoryPassage],
        cca_passages: list[RegulatoryPassage],
    ) -> list[Finding]:
        prompt = build_unit_analysis_prompt(
            unit=unit,
            analysis_input_summary=analysis_input_summary,
            fca_passages=fca_passages,
            cca_passages=cca_passages,
        )
        structured = self._model().with_structured_output(
            _UnitFindingsOutput, method="json_schema"
        )
        result: _UnitFindingsOutput = await structured.ainvoke(prompt)
        return [
            Finding(
                unit_id=unit.unit_id,
                location=unit.location,
                title=draft.title,
                description=draft.description,
                severity=draft.severity,
                status=draft.status,
                evidence_excerpt=draft.evidence_excerpt,
                fca_citations=draft.fca_citations,
                cca_citations=draft.cca_citations,
                fca_vs_cca_comparison=draft.fca_vs_cca_comparison,
                remediation_guidance=draft.remediation_guidance,
                suggested_change=draft.suggested_change,
                confidence=draft.confidence,
            )
            for draft in result.findings
        ]

    async def synthesize_executive_summary(
        self,
        *,
        analysis_input: AnalysisInput,
        findings: list[Finding],
    ) -> str:
        prompt = build_executive_summary_prompt(
            analysis_input=analysis_input,
            findings=findings,
        )
        response = await self._model().ainvoke(prompt)
        return str(response.text).strip()  # `content` can be a list of blocks
