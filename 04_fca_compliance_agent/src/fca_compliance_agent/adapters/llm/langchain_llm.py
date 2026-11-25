"""LangChain-backed ComplianceAnalyzerPort using the repo's LangSmith LLM Gateway."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import httpx
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


_NON_CHAT_MODEL_TOKENS = (
    "image",
    "whisper",
    "tts",
    "realtime",
    "transcribe",
    "moderation",
    "embedding",
    "audio",
    "sora",
    "dall-e",
    "live-",
    "search-api",
    "codex",
    "babbage",
    "davinci",
)
_DATED_SUFFIX_RE = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def list_available_models(*, timeout: float = 10.0) -> list[dict[str, str]]:
    """Query the configured LangSmith LLM Gateway for available `gpt-6` chat models."""
    load_repo_env()
    default_model = resolve_default_model_name()
    models: dict[str, dict[str, str]] = {}

    openai_base = (os.environ.get("OPENAI_BASE_URL") or "https://gateway.smith.langchain.com/openai/v1").rstrip("/")
    openai_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LANGSMITH_API_KEY") or ""

    if openai_key:
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(
                    f"{openai_base}/models",
                    headers={"Authorization": f"Bearer {openai_key}"},
                )
                if resp.status_code == 200:
                    payload: Any = resp.json()
                    for item in payload.get("data", []) if isinstance(payload, dict) else []:
                        raw_id = str(item.get("id") or "").strip() if isinstance(item, dict) else ""
                        if not raw_id:
                            continue
                        low = raw_id.lower()
                        if "gpt-6" not in low:
                            continue
                        if any(tok in low for tok in _NON_CHAT_MODEL_TOKENS):
                            continue
                        full_id = raw_id if ":" in raw_id else f"openai:{raw_id}"
                        models[full_id] = {
                            "id": full_id,
                            "provider": full_id.split(":", 1)[0],
                            "name": full_id.split(":", 1)[-1],
                        }
        except Exception:
            pass

    if default_model and "gpt-6" in default_model.lower() and default_model not in models:
        provider, _, name = default_model.partition(":")
        models[default_model] = {
            "id": default_model,
            "provider": provider if name else "openai",
            "name": name or provider,
        }

    def _sort_key(entry: dict[str, str]) -> tuple[int, int, str]:
        is_default = 0 if entry["id"] == default_model else 1
        is_dated = 1 if _DATED_SUFFIX_RE.search(entry["name"]) else 0
        return (is_default, is_dated, entry["id"])

    return sorted(models.values(), key=_sort_key)


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
