"""SQL Agent must escalate hard LLM errors to AUDIT_REQUIRED(reason='system_error')
without silent fallback. Soft errors (output/validation) follow the same path
in R1 — V5: no silent fallback during the product code path; the deterministic
oracle is in tests/_fixtures only.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trust_analytics.agents.quality_agent import QualityAgent
from trust_analytics.agents.sql_agent import SQLAgent
from trust_analytics.llm import (
    LLMAuthError,
    LLMOutputError,
    LLMQuotaError,
    LLMResponse,
    LLMTransientError,
)
from trust_analytics.metadata import DbtMetadata
from trust_analytics.models import ReviewMode, TerminalState, UsageRecord
from trust_analytics.questions import get_question
from trust_analytics.workflow import run_pipeline


@pytest.fixture()
def empty_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "empty.sqlite"
    sqlite3.connect(db_path).close()
    return db_path


@pytest.fixture()
def empty_metadata() -> DbtMetadata:
    return DbtMetadata(sources={}, models={})


class _StubLLMRaises:
    """Test stub that raises a chosen exception every time."""

    def __init__(self, raises: Exception):
        self._raises = raises
        self.available = True

    def chat_json(self, system: str, user: str, *, stage_tag: str = "") -> LLMResponse:
        raise self._raises


class _StubLLMReturns:
    """Test stub that returns a fixed string content."""

    def __init__(self, content: str):
        self._content = content
        self.available = True

    def chat_json(self, system: str, user: str, *, stage_tag: str = "") -> LLMResponse:
        return LLMResponse(content=self._content, usage=UsageRecord())


@pytest.mark.parametrize(
    "exc, expected_class",
    [
        (LLMAuthError("bad key"), "auth"),
        (LLMQuotaError("quota out"), "quota"),
        (LLMTransientError("connection reset"), "transient"),
    ],
)
def test_hard_errors_escalate_with_system_error(
    empty_db: Path,
    empty_metadata: DbtMetadata,
    exc: Exception,
    expected_class: str,
) -> None:
    agent = SQLAgent(
        db_path=empty_db,
        metadata=empty_metadata,
        llm_client=_StubLLMRaises(exc),  # type: ignore[arg-type]
    )

    answer = agent.answer(get_question("q2_gtv_usd_oct_2025"))

    assert answer.system_error is not None
    assert answer.system_error.error_class == expected_class
    assert answer.metric_value is None
    assert answer.sql == ""
    assert answer.source is None


def test_soft_output_errors_also_escalate_no_silent_fallback(
    empty_db: Path,
    empty_metadata: DbtMetadata,
) -> None:
    """Per V5: there is no silent fallback. Bad LLM output becomes
    SystemError(error_class='output')."""
    agent = SQLAgent(
        db_path=empty_db,
        metadata=empty_metadata,
        llm_client=_StubLLMRaises(LLMOutputError("empty content")),  # type: ignore[arg-type]
    )

    answer = agent.answer(get_question("q2_gtv_usd_oct_2025"))

    assert answer.system_error is not None
    assert answer.system_error.error_class == "output"


def test_quality_agent_renders_red_trust_profile_for_system_error(
    empty_db: Path,
    empty_metadata: DbtMetadata,
) -> None:
    agent = SQLAgent(
        db_path=empty_db,
        metadata=empty_metadata,
        llm_client=_StubLLMRaises(LLMQuotaError("quota out")),  # type: ignore[arg-type]
    )
    answer = agent.answer(get_question("q2_gtv_usd_oct_2025"))

    qa = QualityAgent(empty_db).assess(answer)

    assert qa.layer_a.checks
    assert qa.layer_a.checks[0].name == "system_error_quota"
    assert qa.layer_a.checks[0].result == "FAIL"
    assert qa.layer_b.verdict == "NOT_APPLICABLE"
    assert qa.layer_c.trust_profile.overall == "RED"
    assert qa.layer_c.unresolved_questions  # must be non-empty


def test_pipeline_routes_system_error_to_audit_required_with_reason(
    empty_db: Path,
    empty_metadata: DbtMetadata,
) -> None:
    sql_agent = SQLAgent(
        db_path=empty_db,
        metadata=empty_metadata,
        llm_client=_StubLLMRaises(LLMQuotaError("quota out")),  # type: ignore[arg-type]
    )
    quality_agent = QualityAgent(empty_db)

    result = run_pipeline(
        [get_question("q2_gtv_usd_oct_2025")],
        sql_agent,
        quality_agent,
        ReviewMode.DEMO_APPROVE,
    )

    assert len(result.items) == 1
    item = result.items[0]
    decision = item.review_decision
    assert decision is not None
    assert decision.terminal_state == TerminalState.AUDIT_REQUIRED
    assert decision.audit_reason == "system_error"
    assert result.terminal_summary == {"audit_required": 1}
    # Hand-off package must be populated per Decision 6.
    assert item.audit_handoff is not None
    assert item.audit_handoff.unresolved_questions
