"""The `fca_compliance_reviewer` agent: reviews one change against the FCA Handbook and the CCA.

It is the only agent in this package. It chooses its own searches and reading, using five read-only tools, and cannot
post, edit or run anything. Its prompt is `REVIEWER_SYSTEM_PROMPT` in `core/prompts.py`, and `core/review.py` checks
its work in code afterwards.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, Field

from ...core.prompts import REVIEWER_SYSTEM_PROMPT, build_reviewer_prompt
from ...core.review import check_against_passages
from ...domain.models import (
    AnalysisUnit,
    Finding,
    FindingStatus,
    RegulatoryCitation,
    RegulatoryPassage,
    RegulatorySource,
    Severity,
    TriagedUnit,
    UnitReview,
)
from ...ports.regulatory import ConsumerCreditActPort, FCAHandbookResearchPort
from .langchain_llm import resolve_default_model_name

AGENT_NAME = "fca_compliance_reviewer"
MAX_TOOL_CALLS = 15


# The agent's answer. Every field is required, because some providers only accept schemas without defaults.
class _DraftCitation(BaseModel):
    source: RegulatorySource
    reference: str = Field(description="Exactly as a tool returned it, for example 'SYSC 4.1.1R'")
    quote: str = Field(description="4 to 40 words copied word for word from that provision")


class _DraftReviewFinding(BaseModel):
    title: str
    description: str
    severity: Severity
    status: FindingStatus
    evidence_excerpt: str
    citations: list[_DraftCitation]
    fca_vs_cca_comparison: str
    remediation_guidance: str
    suggested_change: str | None
    confidence: float = Field(description="From 0 to 1")


class _ReviewAnswer(BaseModel):
    """The finished review. Give it once you have read the rules and checked every quote."""

    decision: Literal["issues_found", "no_issue", "needs_review"]
    rationale: str
    findings: list[_DraftReviewFinding]


class _Run:
    """One review: its tools, its limit, and a record made by code of what the agent did."""

    def __init__(
        self,
        fca_port: FCAHandbookResearchPort,
        cca_port: ConsumerCreditActPort,
        regulatory_scope: list[str],
        max_tool_calls: int,
    ) -> None:
        self._fca = fca_port
        self._cca = cca_port
        self._scope = {s.upper() for s in regulatory_scope}
        self._limit = max_tool_calls
        self._used = 0
        self.read: list[RegulatoryPassage] = []
        self.calls: list[str] = []
        self.problems: list[str] = []
        self.unsearchable: list[str] = []

    async def _guarded(self, label: str, work: Callable[[], Awaitable[Any]]) -> Any:
        if self._used >= self._limit:
            self._problem(f"the limit of {self._limit} tool calls was reached")
            return "LIMIT REACHED. No more tool calls are possible. Give your answer now, with decision needs_review."
        self._used += 1
        self.calls.append(label)
        try:
            return await work()
        except LookupMiss as miss:  # the agent asked for something that does not exist, and can correct itself
            return f"NOT FOUND: {miss}"
        except Exception as error:
            self._problem(f"a tool failed ({label}): {error}")
            return f"TOOL FAILED: {error}. This is not regulatory text. Do not treat it as evidence."

    def _problem(self, text: str) -> None:
        if text not in self.problems:
            self.problems.append(text)

    def _remember(self, passages: list[RegulatoryPassage]) -> list[dict[str, Any]]:
        self.read += passages
        return [_show(p) for p in passages]

    async def search_fca_handbook(self, query: str, sourcebooks: list[str] | None = None) -> Any:
        """Search the FCA Handbook. Returns candidates with snippets: read them with get_fca_section before judging.

        Use one topic in regulatory words per query. `sourcebooks` narrows it, for example ["SYSC", "PRIN 2A"].
        """

        async def work() -> dict[str, Any]:
            found = await self._fca.search(query, sourcebooks=sourcebooks)
            self.unsearchable += [s for s in found.not_in_index if s.upper() in self._scope]
            return {"candidates": [_show(p) for p in found.passages], "not_in_index": found.not_in_index}

        return await self._guarded(f"FCA:{query}", work)

    async def get_fca_section(self, reference: str) -> Any:
        """Read the whole FCA Handbook section that a provision belongs to, with its definitions and links."""

        async def work() -> dict[str, Any]:
            try:
                section = await self._fca.get_section(reference)
            except Exception as error:
                if "not in force" in str(error) or "not a Handbook reference" in str(error) or "not in the mock" in str(error):
                    raise LookupMiss(str(error)) from error
                raise
            shown = self._remember(section.passages)
            return {**section.model_dump(exclude={"passages"}), "provisions": shown}

        return await self._guarded(f"FCA_SECTION:{reference}", work)

    async def query_consumer_credit_act(self, query: str) -> Any:
        """Search the Consumer Credit Act 1974 by topic. Returns the best passage of each matching section."""

        async def work() -> dict[str, Any]:
            return {"candidates": [_show(p) for p in await self._cca.query_provisions(query)]}

        return await self._guarded(f"CCA:{query}", work)

    async def get_consumer_credit_act_document(self, section: str) -> Any:
        """Read one Consumer Credit Act 1974 section in full, for example "66A"."""

        async def work() -> dict[str, Any]:
            try:
                passages = await self._cca.get_document(section=section)
            except Exception as error:
                if "no section" in str(error):
                    raise LookupMiss(str(error)) from error
                raise
            if not passages:
                raise LookupMiss(f"the Act text has no section {section}")
            return {"provisions": self._remember(passages)}

        return await self._guarded(f"CCA_DOC:{section}", work)

    async def check_citation(self, source: RegulatorySource, reference: str, quote: str) -> Any:
        """Check that a quote is word for word in a provision. Use it before you finish, and fix any that fail."""

        async def work() -> dict[str, Any]:
            if source == "fca_handbook":
                return (await self._fca.check_citation(reference, quote)).model_dump()
            read = [p for p in self.read if p.source == source]
            return check_against_passages(reference, quote, read).model_dump()

        return await self._guarded(f"CHECK:{reference}", work)

    def tools(self) -> list[BaseTool]:
        methods = (
            self.search_fca_handbook,
            self.get_fca_section,
            self.query_consumer_credit_act,
            self.get_consumer_credit_act_document,
            self.check_citation,
        )
        return [StructuredTool.from_function(coroutine=method) for method in methods]


class LookupMiss(LookupError):
    """The agent asked for a provision that is not there. Not a failure of the tool."""


def _show(passage: RegulatoryPassage) -> dict[str, Any]:
    useful = ("provision_type", "binding", "section", "effective_from", "effective_to", "part", "source", "corpus")
    extra = {k: passage.metadata[k] for k in useful if passage.metadata.get(k) is not None}
    return {"reference": passage.reference, "title": passage.title, "text": passage.text, **extra}


class FCAComplianceReviewer:
    """Implements `ComplianceReviewerPort` with a LangChain agent."""

    def __init__(
        self,
        *,
        fca_port: FCAHandbookResearchPort,
        cca_port: ConsumerCreditActPort,
        model: str | BaseChatModel | None = None,
        max_tool_calls: int = MAX_TOOL_CALLS,
        response_strategy: Callable[[type[BaseModel]], Any] | None = None,
    ) -> None:
        self._fca = fca_port
        self._cca = cca_port
        self._model = model
        self._max_tool_calls = max_tool_calls
        self._response_strategy = response_strategy

    def _build(self, run: _Run):
        model = self._model or resolve_default_model_name()
        return create_agent(
            model=init_chat_model(model) if isinstance(model, str) else model,
            tools=run.tools(),
            system_prompt=REVIEWER_SYSTEM_PROMPT.format(max_tool_calls=self._max_tool_calls),
            response_format=(
                self._response_strategy(_ReviewAnswer) if self._response_strategy else _ReviewAnswer
            ),
            name=AGENT_NAME,
        )

    async def review_unit(
        self,
        *,
        unit: AnalysisUnit,
        analysis_input_summary: str,
        triage: TriagedUnit,
        regulatory_scope: list[str],
    ) -> UnitReview:
        run = _Run(self._fca, self._cca, regulatory_scope, self._max_tool_calls)
        prompt = build_reviewer_prompt(
            unit=unit,
            analysis_input_summary=analysis_input_summary,
            triage=triage,
            regulatory_scope=regulatory_scope,
        )
        answer: _ReviewAnswer | None = None
        try:
            result = await self._build(run).ainvoke(
                {"messages": [{"role": "user", "content": prompt}]},
                # Each tool call is at most two steps. The margin leaves room to answer, but not to loop for ever.
                config={"recursion_limit": 2 * self._max_tool_calls + 12, "run_name": AGENT_NAME},
            )
            answer = result.get("structured_response")
        except GraphRecursionError:
            run.problems.append("the agent did not finish within its step limit")
        if answer is None:
            run.problems.append("the agent gave no usable answer")
            return _outcome(run, "needs_review", "", [])
        if answer.decision == "no_issue" and run.unsearchable:
            names = ", ".join(dict.fromkeys(run.unsearchable))
            run.problems.append(f"{names} could not be searched, so a breach of it cannot be ruled out")
        return _outcome(
            run, answer.decision, answer.rationale, [_finding(unit, draft) for draft in answer.findings]
        )


def _outcome(run: _Run, decision: Any, rationale: str, findings: list[Finding]) -> UnitReview:
    return UnitReview(
        decision=decision,
        rationale=rationale,
        findings=findings,
        sections_read=run.read,
        tool_calls=run.calls,
        problems=run.problems,
    )


def _finding(unit: AnalysisUnit, draft: _DraftReviewFinding) -> Finding:
    citations = [
        RegulatoryCitation(source=c.source, reference=c.reference, excerpt=c.quote)
        for c in draft.citations
    ]
    return Finding(
        unit_id=unit.unit_id,
        location=unit.location,
        title=draft.title,
        description=draft.description,
        severity=draft.severity,
        status=draft.status,
        evidence_excerpt=draft.evidence_excerpt,
        fca_citations=[c for c in citations if c.source == "fca_handbook"],
        cca_citations=[c for c in citations if c.source == "consumer_credit_act"],
        fca_vs_cca_comparison=draft.fca_vs_cca_comparison,
        remediation_guidance=draft.remediation_guidance,
        suggested_change=draft.suggested_change,
        confidence=min(1.0, max(0.0, draft.confidence)),
    )
