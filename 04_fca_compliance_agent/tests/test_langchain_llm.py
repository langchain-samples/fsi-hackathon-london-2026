"""The real LLM adapter, with only the model call replaced."""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from fca_compliance_agent import AnalysisInput, LangChainComplianceAnalyzer


class ExecutiveSummaryTest(unittest.IsolatedAsyncioTestCase):
    async def _summary(self, content: Any) -> str:
        analyzer = LangChainComplianceAnalyzer(model_name="openai:not-called")
        model = GenericFakeChatModel(messages=iter([AIMessage(content=content)]))
        with mock.patch.object(analyzer, "_model", return_value=model):
            return await analyzer.synthesize_executive_summary(
                analysis_input=AnalysisInput(target_id="t", source_type="codebase", title="T", units=[]),
                findings=[],
            )

    async def test_the_summary_is_plain_text_however_the_model_returns_it(self) -> None:
        block = {"type": "text", "text": "Fix the default notices.\n\nThen add checks.", "index": 0}
        reasoning = {"type": "reasoning", "summary": [], "index": 0}
        for content in (block["text"], f"  {block['text']}\n", [block], [reasoning, {**block, "index": 1}]):
            with self.subTest(content):
                self.assertEqual(await self._summary(content), block["text"])


if __name__ == "__main__":
    unittest.main()
