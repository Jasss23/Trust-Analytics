"""Guarded SQL Agent."""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import ValidationError

from pluang_agent.baseline import answer_with_baseline
from pluang_agent.db import connect, sqlite_schema_context
from pluang_agent.llm import LLMError, OpenRouterClient
from pluang_agent.metadata import DbtMetadata, compact_metadata_context, metric_hints
from pluang_agent.models import BusinessQuestion, SQLAgentAnswer
from pluang_agent.sql_runner import SQLSafetyError, execute_read_only

SYSTEM_PROMPT = """You are a careful analytics SQL agent.
Return only JSON matching the requested schema. Use source transparency.
If metric definitions or sources are ambiguous, include ambiguity_notes instead of hiding the ambiguity.
Use only read-only SQLite SELECT/WITH SQL."""


class SQLAgent:
    def __init__(
        self,
        db_path: Path,
        metadata: DbtMetadata,
        llm_client: OpenRouterClient | None = None,
        prefer_llm: bool = False,
    ):
        self.db_path = db_path
        self.metadata = metadata
        self.llm_client = llm_client
        self.prefer_llm = prefer_llm

    def answer(
        self,
        question: BusinessQuestion,
        reviewer_note: str | None = None,
    ) -> SQLAgentAnswer:
        if self.prefer_llm and self.llm_client and self.llm_client.available:
            try:
                return self._answer_with_llm(question, reviewer_note)
            except (LLMError, SQLSafetyError, ValidationError, json.JSONDecodeError) as exc:
                fallback = answer_with_baseline(self.db_path, question)
                fallback.warnings.append(
                    f"LLM path failed and deterministic fallback was used: {type(exc).__name__}: {exc}"
                )
                return fallback
        return answer_with_baseline(self.db_path, question)

    def _answer_with_llm(
        self,
        question: BusinessQuestion,
        reviewer_note: str | None,
    ) -> SQLAgentAnswer:
        user_prompt = self._build_prompt(question, reviewer_note)
        assert self.llm_client is not None
        response = self.llm_client.chat_json(SYSTEM_PROMPT, user_prompt)
        payload = _loads_json_object(response.content)
        answer = SQLAgentAnswer.model_validate(payload)
        answer.usage = response.usage
        rows = execute_read_only(self.db_path, answer.sql)
        answer.result_rows = rows
        if not answer.metric_value:
            answer.metric_value = rows
        return answer

    def _build_prompt(self, question: BusinessQuestion, reviewer_note: str | None) -> str:
        with connect(self.db_path) as conn:
            sqlite_context = sqlite_schema_context(conn)
        reviewer_context = f"\nReviewer note for retry: {reviewer_note}" if reviewer_note else ""
        return f"""
Business question:
{question.text}

Question id: {question.id}
Metric: {question.metric}
Period: {question.period}
{reviewer_context}

SQLite schema:
{sqlite_context}

dbt/source metadata:
{compact_metadata_context(self.metadata)}

Metric hints:
{metric_hints()}

Return JSON with keys:
question_id, question, metric_name, metric_value, period, source_tables, filters,
assumptions, logic, sql, result_rows, ambiguity_notes, warnings.
Set result_rows to [] because the application will execute the SQL.
""".strip()


def _loads_json_object(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))

