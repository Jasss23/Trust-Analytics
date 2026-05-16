"""sql_attempt.run_one_attempt tests (R5).

Bundles agent call + execute + pre-flight into one state-machine-callable
unit. Exercises each branch with stub LLM clients and a real SQLite database.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from trust_analytics.agents.sql_agent import SQLAgent
from trust_analytics.llm import LLMOutputError, LLMQuotaError, LLMResponse
from trust_analytics.metadata import DbtMetadata
from trust_analytics.metrics import MetricEntry, MetricsRegistry, SourceSpec
from trust_analytics.models import BusinessQuestion, UsageRecord
from trust_analytics.sql_attempt import (
    is_retry_eligible,
    mark_budget_exhausted,
    run_one_attempt,
)


def _question(qid: str = "test_q") -> BusinessQuestion:
    return BusinessQuestion(id=qid, text="test?", metric="test_metric", period="October 2025")


def _entry(qid: str = "test_q") -> MetricEntry:
    return MetricEntry(
        id=qid,
        metric_name="test_metric",
        cross_source="disabled",
        period_start="2025-10-01",
        period_end="2025-11-01",
        primary=SourceSpec(
            table="some_table",
            column="value",
            period_column="d",
            extra_filters=(),
            breakdown=None,
            aggregator="SUM",
        ),
    )


def _registry(qid: str = "test_q") -> MetricsRegistry:
    return MetricsRegistry(entries={qid: _entry(qid)})


def _empty_metadata() -> DbtMetadata:
    return DbtMetadata(sources={}, models={})


@pytest.fixture()
def real_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE some_table (id INTEGER, value REAL, d TEXT)")
    conn.execute(
        "INSERT INTO some_table VALUES (1, 1000.0, '2025-10-15'), (2, 2000.0, '2025-10-20')"
    )
    conn.commit()
    conn.close()
    return db_path


class _StubReturns:
    def __init__(self, content: str):
        self._content = content
        self.available = True

    def chat_json(self, system: str, user: str, *, stage_tag: str = "") -> LLMResponse:
        return LLMResponse(content=self._content, usage=UsageRecord())


class _StubReturnsSequence:
    """Returns a different fixed content per call (first, second, ...)."""

    def __init__(self, contents: list[str]):
        self._contents = list(contents)
        self.calls: list[tuple[str, str, str]] = []
        self.available = True

    def chat_json(self, system: str, user: str, *, stage_tag: str = "") -> LLMResponse:
        self.calls.append((system, user, stage_tag))
        content = self._contents.pop(0)
        return LLMResponse(content=content, usage=UsageRecord())


class _StubRaises:
    def __init__(self, exc: Exception):
        self._exc = exc
        self.available = True

    def chat_json(self, system: str, user: str, *, stage_tag: str = "") -> LLMResponse:
        raise self._exc


def _good_answer_json(sql: str = "SELECT id, value FROM some_table WHERE d >= '2025-10-01' AND d < '2025-11-01'") -> str:
    return json.dumps(
        {
            "question_id": "test_q",
            "question": "test?",
            "metric_name": "test_metric",
            "metric_value": None,
            "period": "October 2025",
            "source": {
                "primary_table": "some_table",
                "why_chosen": "Per registry primary.",
                "alternatives_available": [],
            },
            "sql": sql,
            "filters": ["d in October 2025"],
            "assumptions": [],
            "logic": "Pull value rows in October.",
            "result_rows": [],
            "interpretation_choices": [],
            "dq_notes": [],
            "warnings": [],
        }
    )


def test_success_path(real_db: Path) -> None:
    agent = SQLAgent(
        db_path=real_db,
        metadata=_empty_metadata(),
        llm_client=_StubReturns(_good_answer_json()),  # type: ignore[arg-type]
    )
    outcome = run_one_attempt(
        sql_agent=agent,
        question=_question(),
        db_path=real_db,
        registry=_registry(),
    )
    assert outcome.status == "success"
    assert outcome.answer.result_rows
    assert outcome.correction_context is None
    assert is_retry_eligible(outcome) is False


def test_exec_failure_produces_schema_hint(real_db: Path) -> None:
    """SQL references a missing column → exec_failure + correction_context
    carries live PRAGMA info on the right table."""
    bad_sql = "SELECT does_not_exist FROM some_table"
    agent = SQLAgent(
        db_path=real_db,
        metadata=_empty_metadata(),
        llm_client=_StubReturns(_good_answer_json(sql=bad_sql)),  # type: ignore[arg-type]
    )
    outcome = run_one_attempt(
        sql_agent=agent,
        question=_question(),
        db_path=real_db,
        registry=_registry(),
    )
    assert outcome.status == "exec_failure"
    assert outcome.correction_context is not None
    assert outcome.correction_context.prev_sql == bad_sql
    assert outcome.correction_context.failure_kind == "exec_failure"
    # Live schema hint must include the table actually referenced
    assert outcome.correction_context.schema_hint is not None
    assert "some_table" in outcome.correction_context.schema_hint
    assert "value" in outcome.correction_context.schema_hint  # real column
    assert is_retry_eligible(outcome) is True


def test_pre_flight_failure_on_empty_result(real_db: Path) -> None:
    """SQL is valid but returns no rows → pre_flight_failure."""
    sql = "SELECT value FROM some_table WHERE d = '2099-12-31'"
    agent = SQLAgent(
        db_path=real_db,
        metadata=_empty_metadata(),
        llm_client=_StubReturns(_good_answer_json(sql=sql)),  # type: ignore[arg-type]
    )
    outcome = run_one_attempt(
        sql_agent=agent,
        question=_question(),
        db_path=real_db,
        registry=_registry(),
    )
    assert outcome.status == "pre_flight_failure"
    assert outcome.correction_context is not None
    assert "empty_result" in outcome.correction_context.failure_detail
    assert is_retry_eligible(outcome) is True


def test_llm_hard_failure_no_retry(real_db: Path) -> None:
    """Auth/quota/transient errors are NOT retry-eligible."""
    agent = SQLAgent(
        db_path=real_db,
        metadata=_empty_metadata(),
        llm_client=_StubRaises(LLMQuotaError("quota out")),  # type: ignore[arg-type]
    )
    outcome = run_one_attempt(
        sql_agent=agent,
        question=_question(),
        db_path=real_db,
        registry=_registry(),
    )
    assert outcome.status == "llm_hard_failure"
    assert outcome.correction_context is None
    assert is_retry_eligible(outcome) is False
    assert outcome.answer.system_error is not None
    assert outcome.answer.system_error.error_class == "quota"


def test_llm_soft_failure_is_retry_eligible(real_db: Path) -> None:
    """LLMOutputError → soft failure, retry-eligible."""
    agent = SQLAgent(
        db_path=real_db,
        metadata=_empty_metadata(),
        llm_client=_StubRaises(LLMOutputError("empty content")),  # type: ignore[arg-type]
    )
    outcome = run_one_attempt(
        sql_agent=agent,
        question=_question(),
        db_path=real_db,
        registry=_registry(),
    )
    assert outcome.status == "llm_soft_failure"
    assert outcome.correction_context is not None
    assert is_retry_eligible(outcome) is True


def test_retry_loop_recovers_with_schema_hint(real_db: Path) -> None:
    """First attempt picks a wrong column; retry with correction context
    succeeds. Verifies the workflow's loop semantics work end-to-end."""
    bad = _good_answer_json(sql="SELECT does_not_exist FROM some_table")
    good = _good_answer_json()
    stub = _StubReturnsSequence([bad, good])
    agent = SQLAgent(
        db_path=real_db,
        metadata=_empty_metadata(),
        llm_client=stub,  # type: ignore[arg-type]
    )

    first = run_one_attempt(
        sql_agent=agent,
        question=_question(),
        db_path=real_db,
        registry=_registry(),
    )
    assert first.status == "exec_failure"

    second = run_one_attempt(
        sql_agent=agent,
        question=_question(),
        db_path=real_db,
        registry=_registry(),
        correction_context=first.correction_context,
    )
    assert second.status == "success"
    # Second call must carry the retry stage tag
    assert any("sql_agent_retry:" in tag for (_, _, tag) in stub.calls[1:])


def test_mark_budget_exhausted_stamps_system_error(real_db: Path) -> None:
    """After budget runs out, mark_budget_exhausted converts the failed
    outcome into a SystemError on the answer."""
    sql = "SELECT value FROM some_table WHERE d = '2099-12-31'"
    agent = SQLAgent(
        db_path=real_db,
        metadata=_empty_metadata(),
        llm_client=_StubReturns(_good_answer_json(sql=sql)),  # type: ignore[arg-type]
    )
    outcome = run_one_attempt(
        sql_agent=agent,
        question=_question(),
        db_path=real_db,
        registry=_registry(),
    )
    assert outcome.status == "pre_flight_failure"
    mark_budget_exhausted(outcome.answer, outcome)
    assert outcome.answer.system_error is not None
    assert outcome.answer.system_error.error_class == "auto_retry_exhausted"
